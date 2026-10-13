"""Backoff exponencial con jitter y circuit breaker (CLOSED, OPEN, HALF_OPEN).

El breaker se usa solo desde el event loop (sin hilos): no necesita candados. El reloj y el azar se inyectan para pruebas deterministas.
"""
import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from app.video.domain.errors import CircuitBreakerOpenError, InvalidConfigurationError, StreamTimeoutError, redact_secrets
from app.video.observability import log_event

logger = logging.getLogger("guardia.video.resilience")
T = TypeVar("T")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Espera = tope exponencial entre [(1-jitter)·tope, tope]: evita que muchas cámaras reintenten a la vez."""

    base_s: float = 0.5
    factor: float = 2.0
    max_s: float = 30.0
    jitter: float = 0.5

    def __post_init__(self) -> None:
        if not (self.base_s > 0 and self.factor >= 1 and self.max_s >= self.base_s and 0 <= self.jitter <= 1):
            raise InvalidConfigurationError("BackoffPolicy inválida")

    def delay(self, attempt: int, rng: random.Random) -> float:
        raw = min(self.max_s, self.base_s * self.factor ** max(0, min(attempt, 64)))
        return raw * (1 - self.jitter) + rng.random() * raw * self.jitter


@dataclass(frozen=True, slots=True)
class BreakerConfig:
    failure_threshold: int = 5  # fallos consecutivos para abrir
    open_duration_s: float = 30.0  # tiempo abierto antes de permitir un sondeo
    half_open_max_calls: int = 1  # sondeos simultáneos en HALF_OPEN
    attempt_timeout_s: float = 10.0  # timeout por intento en `call`

    def __post_init__(self) -> None:
        if not (self.failure_threshold >= 1 and self.open_duration_s > 0 and self.half_open_max_calls >= 1 and self.attempt_timeout_s > 0):
            raise InvalidConfigurationError("BreakerConfig inválida")


Transition = Callable[[str, CircuitState, CircuitState, str], None]


class CircuitBreaker:
    def __init__(
        self, name: str, config: BreakerConfig | None = None, *, camera_id: str | None = None,
        clock: Callable[[], float] = time.monotonic, on_transition: Transition | None = None,
    ) -> None:
        self.name, self.config, self._camera_id, self._clock, self._on_transition = name, config or BreakerConfig(), camera_id, clock, on_transition
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probes = 0
        self.opened_count = 0
        self.last_cause = ""

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    def _transition(self, new: CircuitState, cause: str) -> None:
        old, self._state = self._state, new
        self.last_cause = redact_secrets(cause)[:200]
        log_event(logger, logging.WARNING if new is CircuitState.OPEN else logging.INFO, "circuit_transition",
                  breaker=self.name, camera_id=self._camera_id, frm=old.value, to=new.value, cause=self.last_cause)
        if self._on_transition:
            self._on_transition(self.name, old, new, self.last_cause)

    def allow(self) -> None:
        """Lanza CircuitBreakerOpenError si no se debe intentar ahora. Si permite en HALF_OPEN, reserva un sondeo."""
        now = self._clock()
        if self._state is CircuitState.OPEN:
            remaining = self._opened_at + self.config.open_duration_s - now
            if remaining > 0:
                raise CircuitBreakerOpenError(f"circuito '{self.name}' abierto", retry_after_s=remaining, camera_id=self._camera_id)
            self._probes = 0
            self._transition(CircuitState.HALF_OPEN, "open_duration_elapsed")
        if self._state is CircuitState.HALF_OPEN:
            if self._probes >= self.config.half_open_max_calls:
                raise CircuitBreakerOpenError(f"circuito '{self.name}' en sondeo", retry_after_s=0.05, camera_id=self._camera_id)
            self._probes += 1

    def record_success(self) -> None:
        if self._state is CircuitState.HALF_OPEN:
            self._probes = max(0, self._probes - 1)
            self._failures = 0
            self._transition(CircuitState.CLOSED, "probe_succeeded")
        elif self._state is CircuitState.CLOSED:
            self._failures = 0

    def record_failure(self, cause: str) -> None:
        if self._state is CircuitState.OPEN:
            return
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN:
            self._probes = max(0, self._probes - 1)
            self._open(f"probe_failed: {cause}")
        elif self._failures >= self.config.failure_threshold:
            self._open(f"{self._failures} fallos consecutivos: {cause}")

    def release_probe(self) -> None:
        """Libera un sondeo que no llegó a resolverse (por ejemplo, una cancelación)."""
        if self._state is CircuitState.HALF_OPEN:
            self._probes = max(0, self._probes - 1)

    def _open(self, cause: str) -> None:
        self._opened_at = self._clock()
        self.opened_count += 1
        self._transition(CircuitState.OPEN, cause)

    async def call(self, fn: Callable[[], Awaitable[T]], timeout_s: float | None = None) -> T:
        """Ejecuta `fn` bajo el breaker, con timeout. Los fallos y timeouts cuentan; las cancelaciones no."""
        self.allow()
        try:
            result = await asyncio.wait_for(fn(), timeout_s if timeout_s is not None else self.config.attempt_timeout_s)
        except asyncio.CancelledError:
            self.release_probe()
            raise
        except TimeoutError as exc:
            self.record_failure("timeout")
            raise StreamTimeoutError(f"'{self.name}' superó el timeout", camera_id=self._camera_id) from exc
        except Exception as exc:
            self.record_failure(f"{type(exc).__name__}: {exc}")
            raise
        self.record_success()
        return result
