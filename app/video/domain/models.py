"""Modelos inmutables y estrictos del dominio de video. Solo metadatos ligeros: los píxeles viven en `frame.py`."""
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator, model_validator

from app.video.domain.errors import EventSerializationError, InvalidConfigurationError
from app.video.domain.limits import (
    MAX_ATTRIBUTE_VALUE_CHARS, MAX_EVENT_ATTRIBUTES, MAX_EVENT_PAYLOAD_BYTES, MAX_FRAME_BYTES, MAX_RECENT_ERRORS,
    MAX_STREAM_MEMORY_BYTES,
)
from app.video.domain.types import (
    BufferCapacity, BufferSize, CameraId, Confidence, Count, ErrorText, EventId, EventName, FrameChannels, FrameHeight,
    FrameWidth, Fps, LatencyMs, Milliseconds, SequenceNumber, TimeoutSeconds, UtcTimestamp,
)

_VIDEO_SUFFIXES = (".mp4", ".avi", ".mkv", ".mov")


class DomainModel(BaseModel):
    """Base: inmutable, sin campos desconocidos, sin coerción y sin eco de valores en los errores."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, validate_assignment=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    BACKOFF = "backoff"
    CIRCUIT_OPEN = "circuit_open"


class FrameGeometry(DomainModel):
    width: FrameWidth
    height: FrameHeight
    channels: FrameChannels = 3

    @property
    def byte_size(self) -> int:
        """Bytes de un frame uint8 con esta geometría."""
        return self.width * self.height * self.channels

    @model_validator(mode="after")
    def _within_frame_budget(self) -> "FrameGeometry":
        if self.byte_size > MAX_FRAME_BYTES:
            raise ValueError("la geometría excede el tamaño máximo de un frame")
        return self


class FrameMeta(DomainModel):
    camera_id: CameraId
    sequence: SequenceNumber
    captured_at: UtcTimestamp
    geometry: FrameGeometry


def _validate_source(kind: str, source: str) -> None:
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in source):
        raise ValueError("source contiene espacios o caracteres de control")
    if kind in ("rtsp", "mjpeg", "snapshot"):
        parts = urlsplit(source)
        allowed = ("rtsp", "rtsps") if kind == "rtsp" else ("http", "https")
        if parts.scheme not in allowed:
            raise ValueError(f"source debe usar el esquema {'/'.join(allowed)}")
        if "@" in parts.netloc:
            raise ValueError("source no debe incluir usuario ni clave: las credenciales van separadas")
        if not parts.hostname:
            raise ValueError("source no tiene host")
        try:
            parts.port
        except ValueError as exc:
            raise ValueError("source tiene un puerto inválido") from exc
    elif kind == "usb":
        if not (source.isascii() and source.isdigit() and int(source) <= 99):
            raise ValueError("el índice usb debe ser un entero entre 0 y 99")
    else:
        if "://" in source or ".." in re.split(r"[\\/]", source) or not source.lower().endswith(_VIDEO_SUFFIXES):
            raise ValueError("source de archivo inválido: se espera una ruta a un video sin '..'")


class CameraConfig(DomainModel):
    camera_id: CameraId
    display_name: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=60)]
    source_kind: Literal["rtsp", "mjpeg", "snapshot", "usb", "file"]
    source: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=512)]
    target_fps: Fps = 10.0
    buffer_capacity: BufferCapacity = 4
    stale_after_ms: Milliseconds = 1000
    connect_timeout_s: TimeoutSeconds = 10.0
    read_timeout_s: TimeoutSeconds = 5.0
    max_geometry: FrameGeometry = Field(default_factory=lambda: FrameGeometry(width=1920, height=1080, channels=3))

    @field_validator("display_name")
    @classmethod
    def _no_control_chars(cls, value: str) -> str:
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("display_name contiene caracteres de control")
        return value

    @model_validator(mode="after")
    def _check_source_and_memory(self) -> "CameraConfig":
        _validate_source(self.source_kind, self.source)
        if self.buffer_capacity * self.max_geometry.byte_size > MAX_STREAM_MEMORY_BYTES:
            raise ValueError("buffer_capacity × max_geometry excede la memoria máxima por stream")
        return self


class CameraMetrics(DomainModel):
    camera_id: CameraId
    state: ConnectionState
    frames_received: Count
    frames_dropped: Count
    frames_processed: Count
    ingest_latency_ms: LatencyMs
    buffer_size: BufferSize
    buffer_capacity: BufferCapacity
    recent_errors: Annotated[tuple[ErrorText, ...], Field(max_length=MAX_RECENT_ERRORS)] = ()
    observed_at: UtcTimestamp

    @model_validator(mode="after")
    def _invariants(self) -> "CameraMetrics":
        if self.buffer_size > self.buffer_capacity:
            raise ValueError("buffer_size no puede superar buffer_capacity")
        if self.frames_processed + self.frames_dropped > self.frames_received:
            raise ValueError("procesados + descartados no pueden superar los recibidos")
        return self


class EventAttribute(DomainModel):
    key: Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,31}$")]
    value: Annotated[str, StringConstraints(strict=True, max_length=MAX_ATTRIBUTE_VALUE_CHARS)]

    @field_validator("value")
    @classmethod
    def _no_control_chars(cls, value: str) -> str:
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("el valor contiene caracteres de control")
        return value


class DetectionEvent(DomainModel):
    schema_version: Literal[1] = 1
    event_id: EventId
    name: EventName
    camera_id: CameraId
    occurred_at: UtcTimestamp
    sequence: SequenceNumber
    confidence: Confidence
    attributes: Annotated[tuple[EventAttribute, ...], Field(max_length=MAX_EVENT_ATTRIBUTES)] = ()

    @model_validator(mode="after")
    def _unique_keys_and_size(self) -> "DetectionEvent":
        keys = [a.key for a in self.attributes]
        if len(keys) != len(set(keys)):
            raise ValueError("las claves de attributes deben ser únicas")
        if len(self.model_dump_json().encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError("el evento serializado excede el tamaño máximo de payload")
        return self


def summarize_validation_error(exc: ValidationError, limit: int = 5) -> str:
    """Resume ubicación y motivo de cada error; nunca incluye el valor recibido."""
    parts = [f"{'.'.join(str(p) for p in e['loc']) or '<raíz>'}: {e['msg']}" for e in exc.errors(include_url=False)[:limit]]
    return "; ".join(parts)


def parse_camera_config(raw: Mapping[str, object]) -> CameraConfig:
    """Valida una configuración externa. En modo estricto los modelos anidados se construyen de forma explícita."""
    camera_id = raw.get("camera_id")
    safe_id = camera_id if isinstance(camera_id, str) and re.fullmatch(r"[a-z0-9_]{1,40}", camera_id) else None
    try:
        data = dict(raw)
        geometry = data.get("max_geometry")
        if isinstance(geometry, Mapping):
            data["max_geometry"] = FrameGeometry.model_validate(geometry)
        return CameraConfig.model_validate(data)
    except ValidationError as exc:
        raise InvalidConfigurationError(
            "Configuración de cámara inválida: " + summarize_validation_error(exc), camera_id=safe_id
        ) from exc


def serialize_event(event: DetectionEvent) -> bytes:
    """JSON compacto en UTF-8. Falla cerrado si el evento no es válido o excede el límite."""
    try:
        data = event.model_dump_json().encode("utf-8")
    except Exception as exc:  # cualquier fallo de serialización se reporta como error de dominio
        raise EventSerializationError("No se pudo serializar el evento") from exc
    if len(data) > MAX_EVENT_PAYLOAD_BYTES:
        raise EventSerializationError("El evento serializado excede el tamaño máximo", size=len(data))
    return data


def deserialize_event(data: bytes) -> DetectionEvent:
    """Valida bytes externos (por ejemplo, un registro del WAL) antes de crear el evento."""
    if not isinstance(data, (bytes, bytearray)):
        raise EventSerializationError("Se esperaban bytes")
    if len(data) > MAX_EVENT_PAYLOAD_BYTES:
        raise EventSerializationError("Payload demasiado grande", size=len(data))
    try:
        return DetectionEvent.model_validate_json(bytes(data), strict=True)
    except ValidationError as exc:
        raise EventSerializationError("Evento inválido: " + summarize_validation_error(exc)) from exc
