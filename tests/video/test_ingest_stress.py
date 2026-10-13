"""Estrés controlado: varias cámaras sanas, intermitentes y muertas a la vez. Con GUARDIA_STRESS=1 dura 20 s en lugar de 2."""
import asyncio
import os
import random
import unittest

from app.video.ingest import IngestManager, StreamState
from tests.support.simulators import FlakySource, ScriptedFactory, Session, capture_threads, make_ingest, wait_until

DURATION = 20.0 if os.environ.get("GUARDIA_STRESS") == "1" else 2.0


class FlakyFactory:
    def __init__(self, seed: int, p_fail: float) -> None:
        self._rng, self._p = random.Random(seed), p_fail
        self.sources: list[FlakySource] = []

    def __call__(self) -> FlakySource:
        source = FlakySource(self._rng, self._p, 0.002)
        self.sources.append(source)
        return source


class MixedFleetStressTest(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_fleet_stays_bounded_isolated_and_clean(self) -> None:
        manager: IngestManager = IngestManager(max_cameras=12)
        healthy = [make_ingest(ScriptedFactory([Session([("endless", 0.001)])]), f"sana{i}", capacity=4) for i in range(3)]
        flaky = [make_ingest(FlakyFactory(100 + i, 0.02), f"flaky{i}", threshold=50, min_stable=0.02) for i in range(3)]
        dead = [make_ingest(ScriptedFactory([Session(open_error=ConnectionError("muerta"))]), f"muerta{i}", threshold=2, open_s=0.2) for i in range(2)]
        for cam in (*healthy, *flaky, *dead):
            manager.add(cam)
        await manager.start_all()
        peak_threads, consumed = 0, {c.camera_id: 0 for c in healthy}
        end = asyncio.get_running_loop().time() + DURATION
        try:
            while asyncio.get_running_loop().time() < end:
                for cam in healthy:
                    if await cam.next_frame(0.05) is not None:
                        consumed[cam.camera_id] += 1
                peak_threads = max(peak_threads, len(capture_threads()))
                for s in manager.stats().values():
                    self.assertLessEqual(s.buffer_size, s.buffer_capacity)  # memoria acotada siempre
                await asyncio.sleep(0.01)
        finally:
            await manager.stop_all()
        self.assertTrue(all(n > 10 for n in consumed.values()), consumed)  # las sanas nunca se degradaron
        self.assertLessEqual(peak_threads, 8)  # una cámara = un hilo como máximo
        self.assertTrue(await wait_until(lambda: not capture_threads()))
        for cam_id, s in manager.stats().items():
            self.assertIs(s.state, StreamState.DISCONNECTED)
            self.assertEqual(s.frames_received, s.frames_processed + s.frames_dropped + s.buffer_size, cam_id)
        self.assertGreater(sum(c.stats().reconnects for c in flaky), 0)  # las intermitentes sí se recuperaron solas


if __name__ == "__main__":
    unittest.main()
