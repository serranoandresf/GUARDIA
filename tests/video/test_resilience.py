import asyncio
import random
import unittest

from app.video.domain.errors import CircuitBreakerOpenError, InvalidConfigurationError, StreamTimeoutError
from app.video.resilience import BackoffPolicy, BreakerConfig, CircuitBreaker, CircuitState
from tests.support.simulators import FakeClock


class BackoffTests(unittest.TestCase):
    def test_bounds_growth_and_cap(self) -> None:
        policy, rng = BackoffPolicy(base_s=1.0, factor=2.0, max_s=8.0, jitter=0.5), random.Random(3)
        for attempt in range(12):
            raw = min(8.0, 2.0 ** attempt)
            for _ in range(50):
                self.assertTrue(raw * 0.5 <= policy.delay(attempt, rng) <= raw)

    def test_no_jitter_is_exact(self) -> None:
        policy = BackoffPolicy(base_s=0.5, factor=2.0, max_s=30.0, jitter=0.0)
        self.assertEqual([policy.delay(i, random.Random()) for i in range(4)], [0.5, 1.0, 2.0, 4.0])

    def test_jitter_desynchronizes_many_clients(self) -> None:
        policy = BackoffPolicy(base_s=1.0, jitter=1.0)
        delays = {round(policy.delay(3, random.Random(seed)), 6) for seed in range(100)}
        self.assertGreater(len(delays), 90)

    def test_huge_attempt_does_not_overflow(self) -> None:
        self.assertLessEqual(BackoffPolicy(max_s=30.0).delay(10_000, random.Random(1)), 30.0)

    def test_invalid_parameters(self) -> None:
        for kwargs in (dict(base_s=0), dict(factor=0.5), dict(base_s=5.0, max_s=1.0), dict(jitter=1.5)):
            with self.assertRaises(InvalidConfigurationError):
                BackoffPolicy(**kwargs)  # type: ignore[arg-type]


class BreakerTests(unittest.TestCase):
    def make(self, threshold: int = 3, open_s: float = 10.0) -> tuple[CircuitBreaker, FakeClock, list[tuple[str, str]]]:
        clock, seen = FakeClock(), []
        breaker = CircuitBreaker("cam", BreakerConfig(threshold, open_s, 1, 1.0), clock=clock,
                                 on_transition=lambda n, old, new, cause: seen.append((old.value, new.value)))
        return breaker, clock, seen

    def test_opens_after_consecutive_failures_only(self) -> None:
        breaker, _, _ = self.make()
        breaker.record_failure("a"); breaker.record_failure("b"); breaker.record_success(); breaker.record_failure("c")
        self.assertIs(breaker.state, CircuitState.CLOSED)
        breaker.record_failure("d"); breaker.record_failure("e")
        self.assertIs(breaker.state, CircuitState.OPEN)
        self.assertEqual(breaker.opened_count, 1)

    def test_open_rejects_with_retry_after_then_half_open_allows_one_probe(self) -> None:
        breaker, clock, seen = self.make()
        for _ in range(3):
            breaker.record_failure("x")
        clock.advance(4)
        with self.assertRaises(CircuitBreakerOpenError) as info:
            breaker.allow()
        self.assertAlmostEqual(info.exception.retry_after_s, 6.0)
        clock.advance(6)
        breaker.allow()  # sondeo permitido
        self.assertIs(breaker.state, CircuitState.HALF_OPEN)
        with self.assertRaises(CircuitBreakerOpenError):
            breaker.allow()  # un segundo sondeo simultáneo se rechaza
        breaker.record_success()
        self.assertIs(breaker.state, CircuitState.CLOSED)
        self.assertEqual(seen, [("closed", "open"), ("open", "half_open"), ("half_open", "closed")])

    def test_failed_probe_reopens_with_new_timer(self) -> None:
        breaker, clock, _ = self.make()
        for _ in range(3):
            breaker.record_failure("x")
        clock.advance(10)
        breaker.allow()
        breaker.record_failure("sigue caída")
        self.assertIs(breaker.state, CircuitState.OPEN)
        self.assertEqual(breaker.opened_count, 2)
        with self.assertRaises(CircuitBreakerOpenError) as info:
            breaker.allow()
        self.assertAlmostEqual(info.exception.retry_after_s, 10.0)

    def test_released_probe_allows_another(self) -> None:
        breaker, clock, _ = self.make()
        for _ in range(3):
            breaker.record_failure("x")
        clock.advance(10)
        breaker.allow()
        breaker.release_probe()
        breaker.allow()

    def test_causes_are_redacted(self) -> None:
        breaker, _, _ = self.make(threshold=1)
        breaker.record_failure("fallo rtsp://admin:SECRETO@10.0.0.1/s")
        self.assertNotIn("SECRETO", breaker.last_cause)

    def test_invalid_config(self) -> None:
        for kwargs in (dict(failure_threshold=0), dict(open_duration_s=0), dict(half_open_max_calls=0), dict(attempt_timeout_s=0)):
            with self.assertRaises(InvalidConfigurationError):
                BreakerConfig(**kwargs)  # type: ignore[arg-type]


class BreakerCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_timeout_and_error_accounting(self) -> None:
        breaker = CircuitBreaker("go2rtc", BreakerConfig(2, 60.0, 1, 0.05))

        async def ok() -> int:
            return 7

        async def slow() -> None:
            await asyncio.sleep(1)

        async def boom() -> None:
            raise ConnectionError("rechazada")

        self.assertEqual(await breaker.call(ok), 7)
        with self.assertRaises(StreamTimeoutError):
            await breaker.call(slow)
        with self.assertRaises(ConnectionError):
            await breaker.call(boom)
        self.assertIs(breaker.state, CircuitState.OPEN)
        called = []

        async def never() -> None:
            called.append(1)

        with self.assertRaises(CircuitBreakerOpenError):
            await breaker.call(never)
        self.assertEqual(called, [])  # abierto: la función ni se invoca

    async def test_cancellation_is_not_a_failure_and_releases_probe(self) -> None:
        clock = FakeClock()
        breaker = CircuitBreaker("x", BreakerConfig(1, 5.0, 1, 5.0), clock=clock)
        breaker.record_failure("x")
        clock.advance(5)

        async def wait_forever() -> None:
            await asyncio.sleep(10)

        task = asyncio.create_task(breaker.call(wait_forever))
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIs(breaker.state, CircuitState.HALF_OPEN)
        breaker.allow()  # el sondeo se liberó


if __name__ == "__main__":
    unittest.main()
