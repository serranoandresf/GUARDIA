from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.video.domain import Frame, FrameGeometry, FrameMeta, InvalidFrameError


def test_from_array_builds_validated_frame(ts: datetime, pixels: np.ndarray) -> None:
    frame = Frame.from_array("entrada", 5, ts, pixels)
    assert frame.meta.geometry == FrameGeometry(width=48, height=32, channels=3)
    assert frame.meta.sequence == 5 and frame.nbytes == 32 * 48 * 3 == frame.meta.geometry.byte_size


def test_pixels_become_read_only(ts: datetime, pixels: np.ndarray) -> None:
    frame = Frame.from_array("entrada", 0, ts, pixels)
    with pytest.raises(ValueError):
        frame.pixels[0, 0, 0] = 1


def test_timestamp_is_normalized_to_utc(ts: datetime, pixels: np.ndarray) -> None:
    local = ts.astimezone(timezone(timedelta(hours=-5)))
    assert Frame.from_array("entrada", 0, local, pixels).meta.captured_at == ts


@pytest.mark.parametrize(
    "bad_pixels",
    [
        np.zeros((32, 48, 3), dtype=np.float32),
        np.zeros((32, 48), dtype=np.uint8),
        np.zeros((8, 8, 3), dtype=np.uint8),
        np.zeros((32, 48, 5), dtype=np.uint8),
        [[0, 0, 0]],
    ],
    ids=["float32", "2d", "demasiado-pequeño", "5-canales", "no-ndarray"],
)
def test_from_array_rejects_invalid_pixels(ts: datetime, bad_pixels: object) -> None:
    with pytest.raises(InvalidFrameError):
        Frame.from_array("entrada", 0, ts, bad_pixels)  # type: ignore[arg-type]


def test_from_array_rejects_bad_metadata(ts: datetime, pixels: np.ndarray) -> None:
    with pytest.raises(InvalidFrameError):
        Frame.from_array("Entrada!", 0, ts, pixels)
    with pytest.raises(InvalidFrameError):
        Frame.from_array("entrada", -1, ts, pixels)
    with pytest.raises(InvalidFrameError):
        Frame.from_array("entrada", 0, datetime(2026, 10, 12), pixels)


def test_direct_construction_checks_shape_and_type(ts: datetime, pixels: np.ndarray) -> None:
    meta = FrameMeta(camera_id="entrada", sequence=0, captured_at=ts, geometry=FrameGeometry(width=64, height=32))
    with pytest.raises(InvalidFrameError):
        Frame(meta=meta, pixels=pixels)  # forma (32, 48, 3) ≠ (32, 64, 3)
    with pytest.raises(InvalidFrameError):
        Frame(meta=meta, pixels="no soy un array")  # type: ignore[arg-type]


def test_errors_carry_camera_id_and_no_pixel_data(ts: datetime) -> None:
    meta = FrameMeta(camera_id="entrada", sequence=0, captured_at=ts, geometry=FrameGeometry(width=64, height=32))
    with pytest.raises(InvalidFrameError) as info:
        Frame(meta=meta, pixels=np.full((10, 10, 3), 7, dtype=np.uint8))
    assert info.value.camera_id == "entrada" and "7, 7" not in str(info.value)
