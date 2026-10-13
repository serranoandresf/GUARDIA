from datetime import datetime, timezone

import numpy as np
import pytest

EVENT_ID = "123e4567-e89b-42d3-a456-426614174000"


@pytest.fixture
def ts() -> datetime:
    return datetime(2026, 10, 12, 15, 30, tzinfo=timezone.utc)


@pytest.fixture
def event_id() -> str:
    return EVENT_ID


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(1234)  # semilla fija: pruebas deterministas


@pytest.fixture
def pixels(rng: np.random.Generator) -> np.ndarray:
    return rng.integers(0, 256, size=(32, 48, 3), dtype=np.uint8)


@pytest.fixture
def camera_raw() -> dict[str, object]:
    return {
        "camera_id": "entrada",
        "display_name": "Entrada principal",
        "source_kind": "rtsp",
        "source": "rtsp://192.168.1.20:554/stream1",
        "target_fps": 10.0,
        "buffer_capacity": 4,
        "stale_after_ms": 500,
        "connect_timeout_s": 10.0,
        "read_timeout_s": 5.0,
    }
