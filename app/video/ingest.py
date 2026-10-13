"""Ingesta por cámara: un hilo de captura acotado, buffer circular, reconexión con backoff y circuit breaker.

  * Un hilo por cámara como máximo: nunca se crea otro mientras el anterior siga vivo (sin hilos duplicados ni huérfanos acumulados).
  * Backpressure: el productor nunca espera; si el consumidor se atrasa, el buffer descarta lo antiguo.
  * Aislamiento: cada cámara tiene su tarea, su buffer y su breaker; un fallo se queda en su cámara.
  * Fuentes bloqueantes: se leen en el hilo; el watchdog detecta atascos aunque la fuente ignore la señal de parada.
"""
import asyncio
import logging
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Generic, Protocol, TypeVar

import numpy as np
import numpy.typing as npt

from app.video.buffer import FrameRingBuffer, SizedItem
from app.video.domain.errors import (
    BufferOverflowError, CameraNotFoundError, CircuitBreakerOpenError, GuardiaError, InvalidConfigurationError,
    InvalidFrameError, StreamConnectionError, StreamTimeoutError,
)
from app.video.observability import log_event
from app.video.resilience import BackoffPolicy, BreakerConfig, CircuitBreaker, CircuitState

logger = logging.getLogger("guardia.video.ingest")
F = TypeVar("F", bound=SizedItem)


class FrameSource(Protocol):
    """Fuente bloqueante. `read` debe respetar su timeout y lanzar StreamTimeoutError o StreamConnectionError."""

    def open(self) -> None: ...
    def read(self) -> npt.NDArray[np.uint8]: ...
    def close(self) -> None: ...


class IngestSettings(Protocol):
    @property
    def camera_id(self) -> str: ...
    @property
    def buffer_capacity(self) -> int: ...
    @property
    def stale_after_ms(self) -> int: ...
    @property
    def connect_timeout_s(self) -> float: ...
    @property
    def read_timeout_s(self) -> float: ...


class StreamState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    BACKOFF = "backoff"
    CIRCUIT_OPEN = "circuit_open"


@dataclass(frozen=True, slots=True)
class IngestStats:
    camera_id: str
    state: StreamState
    frames_received: int
    frames_dropped: int
    frames_processed: int
    frames_invalid: int
    ingest_latency_ms: float
    buffer_size: int
    buffer_capacity: int
    reconnects: int
    circuit_state: CircuitState
    recent_errors: tuple[str, ...]


class CameraIngest(Generic[F]):
    def __init__(
        self, settings: IngestSettings, *, source_factory: Callable[[], FrameSource],
        frame_factory: Callable[[str, int, datetime, npt.NDArray[np.uint8]], F], max_frame_bytes: int,
        breaker: CircuitBreaker | None = None, backoff: BackoffPolicy | None = None, rng: random.Random | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), monotonic: Callable[[], float] = time.monotonic,
        min_stable_s: float = 5.0, max_consecutive_invalid: int = 30, join_timeout_s: float = 2.0, watchdog_grace_s: float = 0.25,
    ) -> None:
        if max_frame_bytes < 1 or min_stable_s < 0 or max_consecutive_invalid < 1:
            raise InvalidConfigurationError("Parámetros de ingesta inválidos", camera_id=settings.camera_id)
        self._id = settings.camera_id
        self._connect_timeout, self._read_timeout = settings.connect_timeout_s, settings.read_timeout_s
        self._source_factory, self._frame_factory = source_factory, frame_factory
        self._buffer: FrameRingBuffer[F] = FrameRingBuffer(
            settings.buffer_capacity, settings.buffer_capacity * max_frame_bytes, settings.stale_after_ms / 1000.0, self._id)
        self._breaker = breaker or CircuitBreaker(f"camera:{self._id}", BreakerConfig(), camera_id=self._id)
        self._backoff, self._rng = backoff or BackoffPolicy(), rng or random.Random()
        self._clock, self._monotonic = clock, monotonic
        self._min_stable, self._max_invalid, self._join_timeout, self._grace = min_stable_s, max_consecutive_invalid, join_timeout_s, watchdog_grace_s
        self._state = StreamState.DISCONNECTED
        self._seq = self._session_frames = self._invalid = self._reconnects = self._sessions = 0
        self._latency_ms = 0.0
        self._errors: deque[str] = deque(maxlen=10)
        self._last_read = 0.0
        self._worker: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._task: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._closing = False

    # ---------- API pública ----------
    @property
    def camera_id(self) -> str:
        return self._id

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._closing = False
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._supervise(), name=f"ingest-{self._id}")

    async def stop(self) -> None:
        self._closing = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def next_frame(self, timeout_s: float) -> F | None:
        """Espera el frame más reciente (descartando los anteriores). None si vence el tiempo."""
        assert self._wake is not None, "start() no fue llamado"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            frame = self._buffer.take_latest(self._monotonic())
            if frame is not None:
                return frame
            self._wake.clear()
            frame = self._buffer.take_latest(self._monotonic())  # evita perder un aviso entre el chequeo y el clear
            if frame is not None:
                return frame
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(self._wake.wait(), remaining)
            except TimeoutError:
                return None

    def stats(self) -> IngestStats:
        b = self._buffer.stats()
        return IngestStats(self._id, self._state, b.received, b.dropped_total, b.consumed, self._invalid, self._latency_ms,
                           b.size, b.capacity, self._reconnects, self._breaker.state, tuple(self._errors))

    def to_domain_metrics(self, observed_at: datetime) -> Any:
        """Convierte a `CameraMetrics` (requiere Pydantic; importación diferida)."""
        from app.video.domain.models import CameraMetrics, ConnectionState

        s = self.stats()
        return CameraMetrics(
            camera_id=s.camera_id, state=ConnectionState(s.state.value), frames_received=s.frames_received, frames_dropped=s.frames_dropped,
            frames_processed=s.frames_processed, ingest_latency_ms=s.ingest_latency_ms, buffer_size=s.buffer_size,
            buffer_capacity=s.buffer_capacity, recent_errors=s.recent_errors, observed_at=observed_at)

    def worker_alive(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    # ---------- internos ----------
    def _set_state(self, new: StreamState, cause: str = "") -> None:
        if new is not self._state:
            log_event(logger, logging.INFO, "stream_state", camera_id=self._id, frm=self._state.value, to=new.value, cause=cause)
            self._state = new

    def _note_error(self, exc: BaseException) -> None:
        text = (exc.message if isinstance(exc, GuardiaError) else f"{type(exc).__name__}: {exc}")[:200]
        self._errors.append(text or type(exc).__name__)

    def _notify(self, loop: asyncio.AbstractEventLoop) -> None:
        wake = self._wake
        if wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:  # loop ya cerrado
                pass

    async def _join_worker(self, timeout_s: float) -> None:
        worker = self._worker
        if worker is not None and worker.is_alive():
            await asyncio.to_thread(worker.join, timeout_s)

    async def _supervise(self) -> None:
        attempt = 0
        try:
            while not self._closing:
                if self.worker_alive():  # hilo anterior atascado: no se crea otro
                    self._set_state(StreamState.BACKOFF, "worker_alive")
                    await self._join_worker(0.05)
                    await asyncio.sleep(0.05)
                    continue
                try:
                    self._breaker.allow()
                except CircuitBreakerOpenError as exc:
                    self._set_state(StreamState.CIRCUIT_OPEN, "breaker_open")
                    await asyncio.sleep(min(max(exc.retry_after_s, 0.01), 1.0))
                    continue
                self._set_state(StreamState.CONNECTING)
                if self._sessions:
                    self._reconnects += 1
                self._sessions += 1
                error, stable = await self._run_session()
                if error is None:
                    break
                self._note_error(error)
                self._breaker.record_failure(f"{type(error).__name__}: {error}")
                attempt = 0 if stable else attempt
                if self._breaker.state is CircuitState.OPEN:
                    continue
                self._set_state(StreamState.BACKOFF, type(error).__name__)
                await asyncio.sleep(self._backoff.delay(attempt, self._rng))
                attempt += 1
        except asyncio.CancelledError:
            self._stop_evt.set()
            await self._join_worker(self._join_timeout)
            raise
        except Exception as exc:  # un fallo inesperado no debe tumbar el servidor: queda registrado y la cámara en DISCONNECTED
            self._note_error(exc)
            log_event(logger, logging.ERROR, "supervisor_crashed", camera_id=self._id, error=f"{type(exc).__name__}: {exc}")
        finally:
            self._stop_evt.set()
            self._buffer.clear()
            self._set_state(StreamState.DISCONNECTED, "stopped")

    async def _run_session(self) -> tuple[BaseException | None, bool]:
        loop = asyncio.get_running_loop()
        done: asyncio.Future[BaseException | None] = loop.create_future()
        stop = threading.Event()
        started = self._last_read = self._monotonic()
        self._session_frames = 0
        worker = threading.Thread(target=self._capture, args=(stop, loop, done), name=f"capture-{self._id}", daemon=True)
        self._worker, self._stop_evt = worker, stop
        worker.start()
        stable = False
        try:
            while not done.done():
                await asyncio.wait({done}, timeout=min(0.25, max(self._read_timeout / 4, 0.01)))
                if done.done():
                    break
                now = self._monotonic()
                if not stable and self._session_frames > 0:
                    if self._state is not StreamState.CONNECTED:
                        self._set_state(StreamState.CONNECTED)
                    if now - started >= self._min_stable:
                        self._breaker.record_success()
                        stable = True
                limit = self._connect_timeout if self._session_frames == 0 else self._read_timeout
                if now - self._last_read > limit + self._grace:
                    stop.set()
                    await self._join_worker(self._join_timeout)
                    return StreamTimeoutError("La fuente dejó de entregar datos", camera_id=self._id, limit_s=limit), stable
            return done.result(), stable
        except asyncio.CancelledError:
            stop.set()
            raise

    def _capture(self, stop: threading.Event, loop: asyncio.AbstractEventLoop, done: "asyncio.Future[BaseException | None]") -> None:
        source: FrameSource | None = None
        error: BaseException | None = None
        try:
            source = self._source_factory()
            source.open()
            self._last_read = self._monotonic()
            consecutive_invalid = 0
            while not stop.is_set():
                t0 = self._monotonic()
                pixels = source.read()
                t1 = self._monotonic()
                self._last_read = t1
                if stop.is_set():
                    break
                try:
                    frame = self._frame_factory(self._id, self._seq, self._clock(), pixels)
                    self._buffer.push(frame, t1)
                except (InvalidFrameError, BufferOverflowError) as exc:
                    self._invalid += 1
                    consecutive_invalid += 1
                    self._note_error(exc)
                    if consecutive_invalid >= self._max_invalid:
                        raise StreamConnectionError("Demasiados frames inválidos consecutivos", camera_id=self._id) from exc
                    continue
                consecutive_invalid = 0
                self._seq += 1
                self._session_frames += 1
                if self._session_frames == 1:
                    self._set_state(StreamState.CONNECTED, "first_frame")  # el estado refleja el primer frame sin esperar al supervisor
                self._latency_ms = (self._monotonic() - t0) * 1000.0
                self._notify(loop)
        except BaseException as exc:  # noqa: BLE001 - el hilo siempre debe informar su final
            error = None if stop.is_set() else exc
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception as exc:  # noqa: BLE001
                    log_event(logger, logging.WARNING, "source_close_failed", camera_id=self._id, error=f"{type(exc).__name__}: {exc}")
            try:
                loop.call_soon_threadsafe(lambda: done.done() or done.set_result(error))
            except RuntimeError:  # loop cerrado durante el apagado
                pass


class IngestManager(Generic[F]):
    """Agrupa las cámaras. `max_cameras` es también el máximo de hilos de captura."""

    def __init__(self, max_cameras: int = 32) -> None:
        if max_cameras < 1:
            raise InvalidConfigurationError("max_cameras debe ser positivo")
        self._max, self._cameras = max_cameras, {}  # type: int, dict[str, CameraIngest[F]]

    def add(self, ingest: CameraIngest[F]) -> None:
        if ingest.camera_id in self._cameras:
            raise InvalidConfigurationError("Cámara duplicada", camera_id=ingest.camera_id)
        if len(self._cameras) >= self._max:
            raise InvalidConfigurationError("Se alcanzó el máximo de cámaras (hilos de captura)", max_cameras=self._max)
        self._cameras[ingest.camera_id] = ingest

    def get(self, camera_id: str) -> CameraIngest[F]:
        try:
            return self._cameras[camera_id]
        except KeyError:
            raise CameraNotFoundError("Cámara no registrada", camera_id=camera_id) from None

    async def remove(self, camera_id: str) -> None:
        ingest = self.get(camera_id)
        await ingest.stop()
        del self._cameras[camera_id]

    async def start_all(self) -> None:
        for ingest in self._cameras.values():
            await ingest.start()

    async def stop_all(self) -> None:
        await asyncio.gather(*(c.stop() for c in self._cameras.values()), return_exceptions=True)

    def stats(self) -> dict[str, IngestStats]:
        return {cid: c.stats() for cid, c in self._cameras.items()}
