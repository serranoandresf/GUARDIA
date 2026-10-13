from datetime import datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from app.video.domain.types import (
    BufferCapacity, CameraId, Confidence, EventId, EventName, FrameChannels, FrameHeight, FrameWidth, Fps,
    Milliseconds, SequenceNumber, TimeoutSeconds, UtcTimestamp,
)


def ok(tp: object, value: object, **kw: object) -> object:
    return TypeAdapter(tp).validate_python(value, **kw)


def bad(tp: object, value: object, **kw: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(tp).validate_python(value, **kw)


@pytest.mark.parametrize("value", ["entrada", "cam_1", "a", "a" * 40, "0patio"])
def test_camera_id_valid(value: str) -> None:
    assert ok(CameraId, value) == value


@pytest.mark.parametrize("value", ["", "Entrada", "a" * 41, "a b", "ñandú", "a-b", "a\n", "../x", 5])
def test_camera_id_invalid(value: object) -> None:
    bad(CameraId, value)


def test_event_id_valid_and_invalid(event_id: str) -> None:
    assert ok(EventId, event_id) == event_id
    for value in (event_id.upper(), event_id.replace("-", ""), event_id[:-1], event_id + "0", "x" * 36):
        bad(EventId, value)


@pytest.mark.parametrize("value", ["person", "movimiento", "a", "a" * 30, "car_2"])
def test_event_name_valid(value: str) -> None:
    assert ok(EventName, value) == value


@pytest.mark.parametrize("value", ["Person", "1abc", "_x", "a" * 31, "a b", ""])
def test_event_name_invalid(value: str) -> None:
    bad(EventName, value)


def test_timestamp_normalized_to_utc() -> None:
    plus5 = timezone(timedelta(hours=5))
    result = ok(UtcTimestamp, datetime(2026, 10, 12, 20, 30, tzinfo=plus5))
    assert result == datetime(2026, 10, 12, 15, 30, tzinfo=timezone.utc)
    assert result.utcoffset() == timedelta(0)
    assert result.tzinfo is timezone.utc


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 1, 1), datetime(2019, 12, 31, tzinfo=timezone.utc), datetime(2100, 1, 1, tzinfo=timezone.utc)],
)
def test_timestamp_rejects_naive_and_out_of_range(value: datetime) -> None:
    bad(UtcTimestamp, value)


def test_timestamp_rejects_strings_in_strict_mode() -> None:
    bad(UtcTimestamp, "2026-10-12T15:30:00Z", strict=True)


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_confidence_valid(value: float) -> None:
    assert ok(Confidence, value) == value


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf"), "0.5", None])
def test_confidence_invalid(value: object) -> None:
    bad(Confidence, value)


@pytest.mark.parametrize("value", [-1, 2**63, 1.5, "1", True])
def test_sequence_invalid(value: object) -> None:
    bad(SequenceNumber, value)


def test_sequence_valid_bounds() -> None:
    assert ok(SequenceNumber, 0) == 0
    assert ok(SequenceNumber, 2**63 - 1) == 2**63 - 1


@pytest.mark.parametrize(
    ("tp", "good", "wrong"),
    [
        (FrameWidth, (16, 7680), (15, 7681, 0, -1)),
        (FrameHeight, (16, 4320), (15, 4321, 0)),
        (FrameChannels, (1, 4), (0, 5)),
        (BufferCapacity, (1, 64), (0, 65)),
        (Milliseconds, (50, 60_000), (49, 60_001)),
    ],
)
def test_integer_bounds(tp: object, good: tuple[int, ...], wrong: tuple[int, ...]) -> None:
    for value in good:
        assert ok(tp, value) == value
    for value in wrong:
        bad(tp, value)
    bad(tp, "20")
    bad(tp, 20.0)


@pytest.mark.parametrize(("tp", "good", "wrong"), [(Fps, (0.1, 240.0), (0.0, -1.0, 240.1)), (TimeoutSeconds, (0.5, 120.0), (0.0, 120.1))])
def test_float_bounds(tp: object, good: tuple[float, ...], wrong: tuple[float, ...]) -> None:
    for value in good:
        assert ok(tp, value) == value
    for value in (*wrong, float("nan"), float("inf")):
        bad(tp, value)
