import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.video.domain import (
    CameraConfig, CameraMetrics, ConnectionState, DetectionEvent, EventAttribute, EventSerializationError, FrameGeometry,
    FrameMeta, InvalidConfigurationError, deserialize_event, parse_camera_config, serialize_event,
)
from app.video.domain.limits import MAX_EVENT_PAYLOAD_BYTES


# ---------- FrameGeometry / FrameMeta ----------
def test_geometry_byte_size() -> None:
    assert FrameGeometry(width=1920, height=1080, channels=3).byte_size == 6_220_800


def test_geometry_accepts_4k_rgba_and_rejects_8k_rgb() -> None:
    assert FrameGeometry(width=3840, height=2160, channels=4).byte_size == 33_177_600
    with pytest.raises(ValidationError):
        FrameGeometry(width=7680, height=4320, channels=3)


def test_models_are_frozen_and_hashable() -> None:
    geo = FrameGeometry(width=64, height=64)
    with pytest.raises(ValidationError):
        geo.width = 100  # type: ignore[misc]
    assert hash(geo) == hash(FrameGeometry(width=64, height=64))


def test_extra_fields_and_string_numbers_are_rejected() -> None:
    with pytest.raises(ValidationError):
        FrameGeometry(width=64, height=64, channels=3, extra=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        FrameGeometry(width="64", height=64)  # type: ignore[arg-type]


def test_validation_errors_do_not_echo_input() -> None:
    with pytest.raises(ValidationError) as info:
        FrameGeometry(width="VALOR_SECRETO", height=64)  # type: ignore[arg-type]
    assert "VALOR_SECRETO" not in str(info.value)


def test_frame_meta_normalizes_timestamp(ts: datetime) -> None:
    local = ts.astimezone(timezone(timedelta(hours=-5)))
    meta = FrameMeta(camera_id="entrada", sequence=7, captured_at=local, geometry=FrameGeometry(width=64, height=64))
    assert meta.captured_at == ts and meta.captured_at.tzinfo is timezone.utc


def test_frame_meta_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        FrameMeta(camera_id="entrada", sequence=0, captured_at=datetime(2026, 10, 12), geometry=FrameGeometry(width=64, height=64))


# ---------- CameraConfig ----------
def test_camera_config_defaults(camera_raw: dict[str, object]) -> None:
    config = parse_camera_config(camera_raw)
    assert config.max_geometry == FrameGeometry(width=1920, height=1080, channels=3)
    assert config.camera_id == "entrada" and config.buffer_capacity == 4


def test_camera_config_accepts_nested_dict_geometry(camera_raw: dict[str, object]) -> None:
    camera_raw["max_geometry"] = {"width": 1280, "height": 720, "channels": 3}
    assert parse_camera_config(camera_raw).max_geometry.width == 1280


def test_display_name_is_stripped_and_control_chars_rejected(camera_raw: dict[str, object]) -> None:
    camera_raw["display_name"] = "  Entrada  "
    assert parse_camera_config(camera_raw).display_name == "Entrada"
    camera_raw["display_name"] = "Entra\x00da"
    with pytest.raises(InvalidConfigurationError):
        parse_camera_config(camera_raw)


@pytest.mark.parametrize(
    ("kind", "source"),
    [
        ("rtsp", "http://192.168.1.20/stream"),
        ("rtsp", "rtsp://admin:SECRETO@192.168.1.20/stream"),
        ("rtsp", "rtsp:///stream"),
        ("rtsp", "rtsp://host:99999/stream"),
        ("rtsp", "rtsp://host/with space"),
        ("mjpeg", "rtsp://192.168.1.20/stream"),
        ("snapshot", "ftp://192.168.1.20/a.jpg"),
        ("usb", "100"),
        ("usb", "-1"),
        ("usb", "camara"),
        ("file", "../secreto.mp4"),
        ("file", "videos/entrada.txt"),
        ("file", "http://host/a.mp4"),
    ],
)
def test_invalid_sources_are_rejected_without_leaking(camera_raw: dict[str, object], kind: str, source: str) -> None:
    camera_raw.update(source_kind=kind, source=source)
    with pytest.raises(InvalidConfigurationError) as info:
        parse_camera_config(camera_raw)
    assert "SECRETO" not in str(info.value) and "SECRETO" not in json.dumps(info.value.to_log())
    assert info.value.camera_id == "entrada"


@pytest.mark.parametrize(
    ("kind", "source"),
    [("rtsp", "rtsps://cam.local:322/live"), ("mjpeg", "http://10.0.0.5/video.mjpg"), ("snapshot", "https://10.0.0.5/snap.jpg"),
     ("usb", "0"), ("usb", "99"), ("file", "videos/entrada.mp4")],
)
def test_valid_sources(camera_raw: dict[str, object], kind: str, source: str) -> None:
    camera_raw.update(source_kind=kind, source=source)
    assert parse_camera_config(camera_raw).source == source


def test_stream_memory_budget_is_enforced(camera_raw: dict[str, object]) -> None:
    camera_raw.update(buffer_capacity=64, max_geometry={"width": 3840, "height": 2160, "channels": 3})
    with pytest.raises(InvalidConfigurationError):
        parse_camera_config(camera_raw)
    camera_raw.update(buffer_capacity=20)  # 20 × 24,9 MB = 497,7 MB, dentro de 512 MiB
    assert parse_camera_config(camera_raw).buffer_capacity == 20


def test_unknown_and_missing_fields_raise_domain_error(camera_raw: dict[str, object]) -> None:
    with pytest.raises(InvalidConfigurationError):
        parse_camera_config({**camera_raw, "password": "x"})
    del camera_raw["source"]
    with pytest.raises(InvalidConfigurationError):
        parse_camera_config(camera_raw)


def test_camera_config_rejects_string_numbers(camera_raw: dict[str, object]) -> None:
    camera_raw["target_fps"] = "10"
    with pytest.raises(InvalidConfigurationError):
        parse_camera_config(camera_raw)


# ---------- CameraMetrics ----------
def metrics_kwargs(ts: datetime) -> dict[str, object]:
    return dict(camera_id="entrada", state=ConnectionState.CONNECTED, frames_received=100, frames_dropped=10,
                frames_processed=80, ingest_latency_ms=12.5, buffer_size=2, buffer_capacity=4, observed_at=ts)


def test_metrics_valid(ts: datetime) -> None:
    metrics = CameraMetrics(**metrics_kwargs(ts))
    assert metrics.state is ConnectionState.CONNECTED and metrics.recent_errors == ()


@pytest.mark.parametrize(
    "change",
    [dict(buffer_size=5), dict(frames_processed=95), dict(frames_received=-1), dict(ingest_latency_ms=-0.1),
     dict(state="connected"), dict(recent_errors=("e",) * 11), dict(recent_errors=("x" * 201,)), dict(recent_errors=("",))],
)
def test_metrics_invariants(ts: datetime, change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CameraMetrics(**{**metrics_kwargs(ts), **change})


# ---------- DetectionEvent ----------
def make_event(ts: datetime, event_id: str, **change: object) -> DetectionEvent:
    base: dict[str, object] = dict(event_id=event_id, name="person", camera_id="entrada", occurred_at=ts, sequence=42,
                                   confidence=0.87, attributes=(EventAttribute(key="zona", value="patio"),))
    return DetectionEvent(**{**base, **change})


def test_event_roundtrip_is_exact_and_deterministic(ts: datetime, event_id: str) -> None:
    event = make_event(ts, event_id)
    data = serialize_event(event)
    assert data == serialize_event(event) and len(data) <= MAX_EVENT_PAYLOAD_BYTES
    assert deserialize_event(data) == event


def test_event_rejects_naive_timestamp_and_normalizes(ts: datetime, event_id: str) -> None:
    with pytest.raises(ValidationError):
        make_event(ts, event_id, occurred_at=datetime(2026, 10, 12, 15, 30))
    local = ts.astimezone(timezone(timedelta(hours=2)))
    assert make_event(ts, event_id, occurred_at=local).occurred_at == ts


def test_event_rejects_duplicate_keys_and_too_many_attributes(ts: datetime, event_id: str) -> None:
    dup = (EventAttribute(key="a", value="1"), EventAttribute(key="a", value="2"))
    with pytest.raises(ValidationError):
        make_event(ts, event_id, attributes=dup)
    many = tuple(EventAttribute(key=f"k{i:02d}", value="v") for i in range(17))
    with pytest.raises(ValidationError):
        make_event(ts, event_id, attributes=many)


def test_event_rejects_oversized_payload(ts: datetime, event_id: str) -> None:
    big = tuple(EventAttribute(key=f"k{i:02d}", value="x" * 256) for i in range(16))
    with pytest.raises(ValidationError):
        make_event(ts, event_id, attributes=big)


def test_attribute_rejects_control_chars_and_bad_keys() -> None:
    for kwargs in (dict(key="a", value="x\x00y"), dict(key="A", value="x"), dict(key="1a", value="x"), dict(key="a", value="x" * 257)):
        with pytest.raises(ValidationError):
            EventAttribute(**kwargs)


@pytest.mark.parametrize("data", [b"", b"{}", b"[]", b"\xff\xfe", b"no es json", b"x" * (MAX_EVENT_PAYLOAD_BYTES + 1)])
def test_deserialize_rejects_garbage(data: bytes) -> None:
    with pytest.raises(EventSerializationError):
        deserialize_event(data)


def test_deserialize_rejects_unknown_fields_naive_dates_and_non_bytes(ts: datetime, event_id: str) -> None:
    document = json.loads(serialize_event(make_event(ts, event_id)))
    with pytest.raises(EventSerializationError):
        deserialize_event(json.dumps({**document, "extra": 1}).encode())
    with pytest.raises(EventSerializationError):
        deserialize_event(json.dumps({**document, "occurred_at": "2026-10-12T15:30:00"}).encode())
    with pytest.raises(EventSerializationError):
        deserialize_event("texto")  # type: ignore[arg-type]


def test_serialize_rejects_non_events() -> None:
    with pytest.raises(EventSerializationError):
        serialize_event(object())  # type: ignore[arg-type]
