"""Excepciones de dominio del motor de video. No dependen de Pydantic.

Todo mensaje y contexto pasa por `redact_secrets`: ninguna excepción puede filtrar credenciales a los registros.
"""
import re
from typing import ClassVar

_URI_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/@]+@")
_SECRET_PARAM = re.compile(r"(?i)\b(pass(?:word)?|token|secret|clave|code|codigo)=([^&\s]+)")

Scalar = str | int | float | bool | None


def redact_secrets(text: str) -> str:
    """Oculta usuario:clave en URIs y parámetros típicos de secretos."""
    return _SECRET_PARAM.sub(r"\1=***", _URI_USERINFO.sub(r"\1***@", text))


class GuardiaError(Exception):
    """Base de las excepciones de dominio. `code` es estable y apto para métricas y registros."""

    code: ClassVar[str] = "guardia_error"

    def __init__(self, message: str, *, camera_id: str | None = None, **context: Scalar) -> None:
        safe = redact_secrets(message)
        super().__init__(safe)
        self.message: str = safe
        self.camera_id: str | None = camera_id
        self.context: dict[str, Scalar] = {
            key: redact_secrets(value) if isinstance(value, str) else value for key, value in context.items()
        }

    def to_log(self) -> dict[str, object]:
        return {"error": self.code, "message": self.message, "camera_id": self.camera_id, "context": dict(self.context)}


class CameraNotFoundError(GuardiaError):
    code = "camera_not_found"


class InvalidConfigurationError(GuardiaError):
    code = "invalid_configuration"


class InvalidFrameError(GuardiaError):
    code = "invalid_frame"


class BufferOverflowError(GuardiaError):
    code = "buffer_overflow"


class StreamConnectionError(GuardiaError):
    code = "stream_connection"


class StreamTimeoutError(GuardiaError):
    code = "stream_timeout"


class CircuitBreakerOpenError(GuardiaError):
    code = "circuit_open"

    def __init__(self, message: str, *, retry_after_s: float, camera_id: str | None = None) -> None:
        super().__init__(message, camera_id=camera_id, retry_after_s=retry_after_s)
        self.retry_after_s: float = retry_after_s


class EventSerializationError(GuardiaError):
    code = "event_serialization"


class WalError(GuardiaError):
    code = "wal_error"
