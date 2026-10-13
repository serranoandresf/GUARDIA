"""Tipos de dominio validados. Los formatos coinciden con los del servidor actual (ids de cámara, UUID de evento, tipo de alerta)."""
from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, Field, StringConstraints

from app.video.domain.limits import (
    MAX_BUFFER_CAPACITY, MAX_ERROR_TEXT_CHARS, MAX_FPS, MAX_FRAME_BYTES, MAX_FRAME_CHANNELS, MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH, MAX_LATENCY_MS, MAX_SEQUENCE, MAX_STALE_MS, MAX_TIMEOUT_SECONDS, MAX_TIMESTAMP,
    MIN_FRAME_DIMENSION, MIN_STALE_MS, MIN_TIMESTAMP,
)

CameraId = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z0-9_]{1,40}$")]
EventId = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]
EventName = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,29}$")]
ErrorText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=MAX_ERROR_TEXT_CHARS)]


def _normalize_utc(value: datetime) -> datetime:
    utc = value.astimezone(timezone.utc)
    if not MIN_TIMESTAMP <= utc < MAX_TIMESTAMP:
        raise ValueError("timestamp fuera del rango permitido (2020-2100)")
    return utc


UtcTimestamp = Annotated[AwareDatetime, AfterValidator(_normalize_utc)]
Confidence = Annotated[float, Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)]
SequenceNumber = Annotated[int, Field(strict=True, ge=0, le=MAX_SEQUENCE)]
Count = Annotated[int, Field(strict=True, ge=0, le=MAX_SEQUENCE)]

FrameWidth = Annotated[int, Field(strict=True, ge=MIN_FRAME_DIMENSION, le=MAX_FRAME_WIDTH)]
FrameHeight = Annotated[int, Field(strict=True, ge=MIN_FRAME_DIMENSION, le=MAX_FRAME_HEIGHT)]
FrameChannels = Annotated[int, Field(strict=True, ge=1, le=MAX_FRAME_CHANNELS)]
FrameByteSize = Annotated[int, Field(strict=True, ge=0, le=MAX_FRAME_BYTES)]

Fps = Annotated[float, Field(strict=True, gt=0.0, le=MAX_FPS, allow_inf_nan=False)]
TimeoutSeconds = Annotated[float, Field(strict=True, gt=0.0, le=MAX_TIMEOUT_SECONDS, allow_inf_nan=False)]
Milliseconds = Annotated[int, Field(strict=True, ge=MIN_STALE_MS, le=MAX_STALE_MS)]
LatencyMs = Annotated[float, Field(strict=True, ge=0.0, le=MAX_LATENCY_MS, allow_inf_nan=False)]
BufferCapacity = Annotated[int, Field(strict=True, ge=1, le=MAX_BUFFER_CAPACITY)]
BufferSize = Annotated[int, Field(strict=True, ge=0, le=MAX_BUFFER_CAPACITY)]
