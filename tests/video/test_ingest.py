import asyncio
import threading
import unittest
from datetime import datetime, timezone

from app.video.domain.errors import CameraNotFoundError, InvalidConfigurationError, StreamConnectionError, StreamTimeoutError
from app.video.ingest import IngestManager, StreamState
from app.video.resilience import CircuitState
from tests.support.simulators import ScriptedFactory, Session, capture_threads, make_ingest, wait_until

ENDLESS = Session([("endless", 0.005)])


def failing(exc: Exception | None = None) -> Session:
    return Session(open_error=exc or StreamConnectionError("sin ruta al host"))


class IngestFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        for _ in range(100):
            if not capture_threads():
                break
            await asyncio.sleep(0.02)
        self.assertEqual(capture_threads(), [], "quedaron hilos de captura vivos")

    async def test_delivers_latest_frames_in_order_with_consistent_stats(self) -> None:
        factory = ScriptedFactory([ENDLESS])
        ingest = make_ingest(factory)
        await ingest.start()
        try:
            first = await ingest.next_frame(2.0)
            await asyncio.sleep(0.1)
            second = await ingest.next_frame(2.0)
            assert first is not None and second is not None
            self.assertGreater(second.sequence, first.sequence)
            self.assertLessEqual((datetime.now(timezone.utc) - second.captured_at).total_seconds(), 2.0)
            s = ingest.stats()
            self.assertIs(s.state, StreamState.CONNECTED)
            self.assertEqual(s.frames_received, s.frames_processed + s.frames_dropped + s.buffer_size)
            self.assertGreaterEqual(s.frames_processed, 2)
            self.assertGreater(s.ingest_latency_ms, 0.0)
        finally:
            await ingest.stop()
        self.assertTrue(all(src.closed for src in factory.sources))
        self.assertIs(ingest.stats().state, StreamState.DISCONNECTED)

    async def test_next_frame_times_out_without_frames(self) -> None:
        ingest = make_ingest(ScriptedFactory([Session([("block", threading.Event(), 0.05)])], repeat_last=True))
        await ingest.start()
        try:
            self.assertIsNone(await ingest.next_frame(0.05))
        finally:
            await ingest.stop()

    async def test_reconnects_after_open_failures_with_backoff(self) -> None:
        factory = ScriptedFactory([failing(), failing(), ENDLESS])
        ingest = make_ingest(factory, threshold=10)
        await ingest.start()
        try:
            self.assertIsNotNone(await ingest.next_frame(3.0))
            s = ingest.stats()
            self.assertEqual((factory.opens, s.reconnects), (3, 2))
            self.assertTrue(any("sin ruta" in e for e in s.recent_errors))
        finally:
            await ingest.stop()

    async def test_circuit_opens_and_stops_hammering_a_dead_camera(self) -> None:
        factory = ScriptedFactory([failing()])
        ingest = make_ingest(factory, threshold=3, open_s=0.3)
        await ingest.start()
        states = set()
        try:
            for _ in range(90):
                states.add(ingest.stats().circuit_state)
                await asyncio.sleep(0.005)
        finally:
            await ingest.stop()
        self.assertIn(CircuitState.OPEN, states)
        self.assertTrue(3 <= factory.opens <= 5, f"intentos: {factory.opens}")  # 3 fallos + como mucho un sondeo

    async def test_one_dead_camera_does_not_affect_the_others(self) -> None:
        dead = make_ingest(ScriptedFactory([failing()]), "muerta", threshold=2, open_s=0.2)
        alive_a = make_ingest(ScriptedFactory([ENDLESS]), "viva_a")
        alive_b = make_ingest(ScriptedFactory([ENDLESS]), "viva_b")
        manager = IngestManager()
        for c in (dead, alive_a, alive_b):
            manager.add(c)
        await manager.start_all()
        try:
            frames = await asyncio.gather(alive_a.next_frame(2.0), alive_b.next_frame(2.0))
            self.assertTrue(all(f is not None for f in frames))
            await wait_until(lambda: dead.stats().circuit_state is CircuitState.OPEN)
            self.assertIs(alive_a.stats().circuit_state, CircuitState.CLOSED)
            self.assertIs(alive_b.stats().state, StreamState.CONNECTED)
            self.assertEqual(alive_a.stats().reconnects, 0)
        finally:
            await manager.stop_all()

    async def test_never_more_than_one_capture_thread_per_camera_while_flapping(self) -> None:
        flap = Session([("frames", 2, 0.001), ("fail", StreamConnectionError("corte"))])
        factory = ScriptedFactory([flap])
        ingest = make_ingest(factory, threshold=1000, base_s=0.001, max_s=0.002)
        await ingest.start()
        peak = 0
        try:
            for _ in range(150):
                peak = max(peak, len(capture_threads()))
                await asyncio.sleep(0.004)
        finally:
            await ingest.stop()
        self.assertLessEqual(peak, 1)
        self.assertGreater(factory.opens, 5)  # hubo muchas reconexiones, siempre de a un hilo

    async def test_graceful_shutdown_releases_threads_sources_and_tasks(self) -> None:
        factories = [ScriptedFactory([ENDLESS]) for _ in range(3)]
        manager = IngestManager()
        for i, f in enumerate(factories):
            manager.add(make_ingest(f, f"cam{i}"))
        await manager.start_all()
        await wait_until(lambda: len(capture_threads()) == 3)
        await manager.stop_all()
        self.assertTrue(await wait_until(lambda: not capture_threads()))
        self.assertTrue(all(s.closed for f in factories for s in f.sources))
        self.assertTrue(all(s.state is StreamState.DISCONNECTED for s in manager.stats().values()))

    async def test_stalled_source_is_detected_and_no_second_thread_is_spawned(self) -> None:
        release = threading.Event()
        stalled = Session([("block", release, 30.0), ("fail", StreamConnectionError("liberada"))])
        factory = ScriptedFactory([stalled, ENDLESS])
        ingest = make_ingest(factory, read_timeout=0.1, connect_timeout=0.1, join_timeout=0.05)
        await ingest.start()
        try:
            self.assertTrue(await wait_until(lambda: any("dejó de entregar" in e for e in ingest.stats().recent_errors), 3.0))
            await asyncio.sleep(0.3)
            self.assertEqual(factory.opens, 1)  # mientras el hilo atascado viva, no se abre otro
            self.assertEqual(len(capture_threads()), 1)
            release.set()  # el hilo atascado termina y la cámara se recupera
            self.assertIsNotNone(await ingest.next_frame(3.0))
            self.assertEqual(factory.opens, 2)
        finally:
            release.set()
            await ingest.stop()

    async def test_corrupt_frames_are_counted_without_reconnecting(self) -> None:
        factory = ScriptedFactory([Session([("corrupt", 2), ("endless", 0.005)])])
        ingest = make_ingest(factory, max_invalid=3)
        await ingest.start()
        try:
            self.assertIsNotNone(await ingest.next_frame(2.0))
            s = ingest.stats()
            self.assertEqual((s.frames_invalid, s.reconnects), (2, 0))
        finally:
            await ingest.stop()

    async def test_too_many_consecutive_corrupt_frames_force_reconnect(self) -> None:
        factory = ScriptedFactory([Session([("corrupt", 10)]), ENDLESS])
        ingest = make_ingest(factory, max_invalid=3, threshold=10)
        await ingest.start()
        try:
            self.assertIsNotNone(await ingest.next_frame(3.0))
            self.assertEqual((factory.opens, ingest.stats().reconnects), (2, 1))
        finally:
            await ingest.stop()

    async def test_slow_consumer_gets_backpressure_by_dropping_old_frames(self) -> None:
        ingest = make_ingest(ScriptedFactory([Session([("endless", 0.0005)])]), capacity=3)
        await ingest.start()
        sizes = []
        try:
            for _ in range(5):
                await ingest.next_frame(1.0)
                await asyncio.sleep(0.08)  # consumidor lento
                sizes.append(ingest.stats().buffer_size)
        finally:
            await ingest.stop()
        s = ingest.stats()
        self.assertLessEqual(max(sizes), 3)
        self.assertGreater(s.frames_dropped, s.frames_processed)
        self.assertEqual(s.frames_received, s.frames_processed + s.frames_dropped + s.buffer_size)

    async def test_start_is_idempotent_and_restartable(self) -> None:
        ingest = make_ingest(ScriptedFactory([ENDLESS]))
        await ingest.start()
        await ingest.start()
        self.assertTrue(await wait_until(lambda: len(capture_threads()) == 1))
        await asyncio.sleep(0.1)
        self.assertEqual(len(capture_threads()), 1)  # la segunda llamada no creó otro hilo
        await ingest.stop()
        await ingest.stop()
        await ingest.start()
        try:
            self.assertIsNotNone(await ingest.next_frame(2.0))
        finally:
            await ingest.stop()

    async def test_domain_metrics_conversion(self) -> None:
        try:
            import pydantic  # noqa: F401
        except ImportError:
            self.skipTest("Pydantic no instalado")
        ingest = make_ingest(ScriptedFactory([ENDLESS]))
        await ingest.start()
        try:
            await ingest.next_frame(2.0)
            metrics = ingest.to_domain_metrics(datetime.now(timezone.utc))
            self.assertEqual(metrics.camera_id, "cam1")
        finally:
            await ingest.stop()


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_contracts(self) -> None:
        manager = IngestManager(max_cameras=2)
        manager.add(make_ingest(ScriptedFactory([ENDLESS]), "a"))
        with self.assertRaises(InvalidConfigurationError):
            manager.add(make_ingest(ScriptedFactory([ENDLESS]), "a"))
        manager.add(make_ingest(ScriptedFactory([ENDLESS]), "b"))
        with self.assertRaises(InvalidConfigurationError):
            manager.add(make_ingest(ScriptedFactory([ENDLESS]), "c"))
        with self.assertRaises(CameraNotFoundError):
            manager.get("zzz")
        await manager.remove("a")
        self.assertEqual(sorted(manager.stats()), ["b"])
        with self.assertRaises(InvalidConfigurationError):
            IngestManager(max_cameras=0)

    async def test_timeout_error_is_a_domain_error(self) -> None:
        self.assertEqual(StreamTimeoutError("x").code, "stream_timeout")


if __name__ == "__main__":
    unittest.main()
