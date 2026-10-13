"""Simuladores deterministas que reemplazan hardware real en CI: cámaras virtuales, relojes, cortes de energía y esperas."""
import asyncio
import os
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.video.domain.errors import InvalidFrameError, StreamConnectionError
from app.video.ingest import CameraIngest
from app.video.resilience import BackoffPolicy, BreakerConfig, CircuitBreaker
from app.video.wal import encode_record


# ---------- frames sintéticos ----------
def synthetic_pixels(seed: int, height: int = 16, width: int = 16, channels: int = 3) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, size=(height, width, channels), dtype=np.uint8)


def moving_square(index: int, size: int = 32) -> np.ndarray:
    frame = np.zeros((size, size, 3), dtype=np.uint8)
    x = index % (size - 8)
    frame[8:16, x:x + 8] = 255
    return frame


@dataclass(frozen=True, slots=True)
class StubFrame:
    camera_id: str
    sequence: int
    captured_at: datetime
    pixels: np.ndarray

    @property
    def nbytes(self) -> int:
        return int(self.pixels.nbytes)


def stub_frame_factory(camera_id: str, sequence: int, captured_at: datetime, pixels: Any) -> StubFrame:
    if not isinstance(pixels, np.ndarray) or pixels.dtype != np.uint8 or pixels.ndim != 3:
        raise InvalidFrameError("frame inválido", camera_id=camera_id)
    return StubFrame(camera_id, sequence, captured_at, pixels)


@dataclass(frozen=True)
class Settings:
    camera_id: str = "cam1"
    buffer_capacity: int = 3
    stale_after_ms: int = 1000
    connect_timeout_s: float = 0.3
    read_timeout_s: float = 0.3


# ---------- cámara virtual con guion ----------
@dataclass
class Session:
    """Una conexión simulada. Pasos: ("frames", n, delay) · ("endless", delay) · ("fail", exc) · ("corrupt", n) · ("block", event, max_s) · ("delay", s)."""

    steps: list[tuple[Any, ...]] = field(default_factory=list)
    open_error: Exception | None = None


class ScriptedSource:
    def __init__(self, session: Session) -> None:
        self._steps = [list(s) for s in session.steps]
        self._open_error = session.open_error
        self.closed = False
        self.produced = 0

    def open(self) -> None:
        if self._open_error is not None:
            raise self._open_error

    def read(self) -> np.ndarray:
        while True:
            if self.closed:
                raise StreamConnectionError("fuente cerrada")
            if not self._steps:
                raise StreamConnectionError("fin del guion")
            step = self._steps[0]
            kind = step[0]
            if kind == "frames":
                if step[1] <= 0:
                    self._steps.pop(0)
                    continue
                step[1] -= 1
                time.sleep(step[2])
                self.produced += 1
                return moving_square(self.produced)
            if kind == "endless":
                time.sleep(step[1])
                self.produced += 1
                return moving_square(self.produced)
            if kind == "corrupt":
                if step[1] <= 0:
                    self._steps.pop(0)
                    continue
                step[1] -= 1
                return np.zeros((2, 2), dtype=np.float32)
            if kind == "delay":
                time.sleep(step[1])
                self._steps.pop(0)
                continue
            if kind == "block":
                step[1].wait(step[2])
                self._steps.pop(0)
                continue
            if kind == "fail":
                self._steps.pop(0)
                raise step[1]
            raise AssertionError(f"paso desconocido {kind}")

    def close(self) -> None:
        self.closed = True


class ScriptedFactory:
    def __init__(self, sessions: list[Session], repeat_last: bool = True) -> None:
        self._sessions, self._repeat = sessions, repeat_last
        self._lock = threading.Lock()
        self.opens = 0
        self.sources: list[ScriptedSource] = []

    def __call__(self) -> ScriptedSource:
        with self._lock:
            index = self.opens
            self.opens += 1
        if index >= len(self._sessions):
            if not self._repeat:
                raise StreamConnectionError("sin más sesiones")
            index = len(self._sessions) - 1
        source = ScriptedSource(Session(list(self._sessions[index].steps), self._sessions[index].open_error))
        self.sources.append(source)
        return source


class FlakySource:
    """Cámara con pérdidas aleatorias reproducibles (semilla fija)."""

    def __init__(self, rng: random.Random, p_fail: float, delay: float) -> None:
        self._rng, self._p, self._delay, self.closed, self._n = rng, p_fail, delay, False, 0

    def open(self) -> None:
        if self._rng.random() < self._p:
            raise StreamConnectionError("apertura fallida simulada")

    def read(self) -> np.ndarray:
        if self.closed:
            raise StreamConnectionError("fuente cerrada")
        time.sleep(self._delay)
        if self._rng.random() < self._p:
            raise StreamConnectionError("pérdida de red simulada")
        self._n += 1
        return moving_square(self._n)

    def close(self) -> None:
        self.closed = True


# ---------- ingestas, relojes y esperas ----------
def make_ingest(factory: Callable[[], Any], camera_id: str = "cam1", *, capacity: int = 3, seed: int = 1, threshold: int = 3,
                open_s: float = 0.15, base_s: float = 0.01, max_s: float = 0.05, read_timeout: float = 0.3, connect_timeout: float = 0.3,
                min_stable: float = 0.05, max_invalid: int = 3, join_timeout: float = 0.5, stale_ms: int = 1000) -> CameraIngest[StubFrame]:
    breaker = CircuitBreaker(f"camera:{camera_id}", BreakerConfig(threshold, open_s, 1, 1.0), camera_id=camera_id)
    return CameraIngest(
        Settings(camera_id, capacity, stale_ms, connect_timeout, read_timeout), source_factory=factory, frame_factory=stub_frame_factory,
        max_frame_bytes=4096, breaker=breaker, backoff=BackoffPolicy(base_s, 2.0, max_s, 0.5), rng=random.Random(seed),
        min_stable_s=min_stable, max_consecutive_invalid=max_invalid, join_timeout_s=join_timeout, watchdog_grace_s=0.05)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def wait_until(predicate: Callable[[], bool], timeout: float = 3.0, step: float = 0.005) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step)
    return predicate()


def capture_threads(prefix: str = "capture-") -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith(prefix) and t.is_alive()]


# ---------- cortes de energía y corrupción ----------
def last_segment(directory: Path) -> Path:
    return sorted(directory.glob("wal-*.log"))[-1]


def simulate_torn_append(directory: Path, rng: random.Random, record_id: str = "enVuelo") -> int:
    """Escribe una fracción de un registro al final del último segmento: lo que deja un corte durante una escritura."""
    blob = encode_record("E", record_id, "2026-10-12T15:30:00+00:00", b"x" * rng.randint(1, 200))
    cut = rng.randint(1, len(blob) - 1)
    with open(last_segment(directory), "ab") as fh:
        fh.write(blob[:cut])
        fh.flush()
        os.fsync(fh.fileno())
    return cut


def flip_byte(path: Path, offset: int) -> None:
    data = bytearray(path.read_bytes())
    data[offset] ^= 0xFF
    path.write_bytes(bytes(data))
