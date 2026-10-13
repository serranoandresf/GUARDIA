import json

import pytest

from app.video.domain import errors
from app.video.domain.errors import (
    BufferOverflowError, CameraNotFoundError, CircuitBreakerOpenError, EventSerializationError, GuardiaError,
    InvalidConfigurationError, InvalidFrameError, StreamConnectionError, StreamTimeoutError, redact_secrets,
)

ALL = (CameraNotFoundError, InvalidConfigurationError, InvalidFrameError, BufferOverflowError, StreamConnectionError,
       StreamTimeoutError, CircuitBreakerOpenError, EventSerializationError)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("rtsp://admin:pw@10.0.0.1/x", "rtsp://***@10.0.0.1/x"),
        ("falló http://u:p@host:8080/a", "falló http://***@host:8080/a"),
        ("password=abc&x=1", "password=***&x=1"),
        ("Token=abc123", "Token=***"),
        ("codigo=123456", "codigo=***"),
        ("sin secretos aquí", "sin secretos aquí"),
    ],
)
def test_redact_secrets(raw: str, expected: str) -> None:
    assert redact_secrets(raw) == expected


def test_codes_are_unique_and_all_inherit_from_base() -> None:
    codes = [cls.code for cls in ALL]
    assert len(set(codes)) == len(codes)
    assert all(issubclass(cls, GuardiaError) for cls in ALL)
    assert {name for name in dir(errors) if name.endswith("Error")} >= {cls.__name__ for cls in ALL}


@pytest.mark.parametrize("cls", [c for c in ALL if c is not CircuitBreakerOpenError])
def test_errors_redact_message_and_context(cls: type[GuardiaError]) -> None:
    err = cls("fallo en rtsp://usuario:SECRETO@10.0.0.1/s", camera_id="entrada", uri="rtsp://a:SECRETO@h/x", intentos=3)
    rendered = str(err) + json.dumps(err.to_log())
    assert "SECRETO" not in rendered
    assert err.to_log()["error"] == cls.code and err.camera_id == "entrada" and err.context["intentos"] == 3


def test_circuit_breaker_open_error_carries_retry_after() -> None:
    err = CircuitBreakerOpenError("circuito abierto", retry_after_s=12.5, camera_id="patio")
    assert err.retry_after_s == 12.5 and err.context["retry_after_s"] == 12.5
    with pytest.raises(GuardiaError):
        raise err


def test_errors_are_catchable_by_specific_and_base_type() -> None:
    with pytest.raises(GuardiaError):
        raise StreamTimeoutError("sin datos", camera_id="x")
    with pytest.raises(StreamTimeoutError):
        raise StreamTimeoutError("sin datos")
