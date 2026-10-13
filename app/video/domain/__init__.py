"""Dominio del motor de video. Importación perezosa: `errors` y `limits` no exigen Pydantic."""
from importlib import import_module
from typing import TYPE_CHECKING, Any

_EXPORTS: dict[str, str] = {
    **{n: "errors" for n in (
        "BufferOverflowError", "CameraNotFoundError", "CircuitBreakerOpenError", "EventSerializationError", "GuardiaError",
        "InvalidConfigurationError", "InvalidFrameError", "StreamConnectionError", "StreamTimeoutError", "WalError",
        "redact_secrets")},
    "Frame": "frame",
    **{n: "models" for n in (
        "CameraConfig", "CameraMetrics", "ConnectionState", "DetectionEvent", "EventAttribute", "FrameGeometry",
        "FrameMeta", "deserialize_event", "parse_camera_config", "serialize_event")},
}
__all__ = sorted(_EXPORTS)

if TYPE_CHECKING:  # pragma: no cover
    from app.video.domain.errors import *  # noqa: F401,F403
    from app.video.domain.frame import Frame  # noqa: F401
    from app.video.domain.models import *  # noqa: F401,F403


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value
