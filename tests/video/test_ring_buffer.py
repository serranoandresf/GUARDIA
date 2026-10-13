import random
import threading
import unittest
from dataclasses import dataclass

from app.video.buffer import FrameRingBuffer
from app.video.domain.errors import BufferOverflowError, InvalidConfigurationError


@dataclass(frozen=True)
class Item:
    n: int
    nbytes: int = 10


def check_invariants(test: unittest.TestCase, buf: FrameRingBuffer[Item]) -> None:
    s = buf.stats()
    test.assertEqual(s.received, s.consumed + s.dropped_total + s.size)
    test.assertLessEqual(s.size, s.capacity)
    test.assertLessEqual(s.bytes_used, s.max_bytes)


class RingBufferTests(unittest.TestCase):
    def make(self, capacity: int = 3, max_bytes: int = 1000, stale: float = 1.0) -> FrameRingBuffer[Item]:
        return FrameRingBuffer(capacity, max_bytes, stale, "cam1")

    def test_capacity_evicts_oldest_and_never_grows(self) -> None:
        buf = self.make(capacity=3)
        evicted = [buf.push(Item(i), now=float(i) * 0.01) for i in range(5)]
        self.assertEqual(evicted, [0, 0, 0, 1, 1])
        self.assertEqual(len(buf), 3)
        self.assertEqual(buf.take_oldest(0.1).n, 2)  # type: ignore[union-attr]
        self.assertEqual(buf.stats().dropped_overflow, 2)
        check_invariants(self, buf)

    def test_memory_budget_evicts(self) -> None:
        buf = self.make(capacity=10, max_bytes=25)
        for i in range(3):
            buf.push(Item(i), 0.0)
        self.assertEqual(len(buf), 2)
        self.assertEqual(buf.stats().bytes_used, 20)

    def test_item_larger_than_budget_raises(self) -> None:
        with self.assertRaises(BufferOverflowError):
            self.make(max_bytes=5).push(Item(1, nbytes=6), 0.0)

    def test_take_latest_supersedes_older_frames(self) -> None:
        buf = self.make()
        for i in range(3):
            buf.push(Item(i), 0.0)
        self.assertEqual(buf.take_latest(0.1).n, 2)  # type: ignore[union-attr]
        s = buf.stats()
        self.assertEqual((s.dropped_superseded, s.size, s.consumed, s.bytes_used), (2, 0, 1, 0))
        self.assertIsNone(buf.take_latest(0.1))
        check_invariants(self, buf)

    def test_stale_frames_are_dropped(self) -> None:
        buf = self.make(stale=0.5)
        buf.push(Item(0), 0.0)
        buf.push(Item(1), 0.4)
        self.assertEqual(buf.take_oldest(0.6).n, 1)  # type: ignore[union-attr]
        buf.push(Item(2), 1.0)
        self.assertIsNone(buf.take_latest(2.0))
        s = buf.stats()
        self.assertEqual(s.dropped_stale, 2)
        check_invariants(self, buf)

    def test_clear_counts_as_dropped(self) -> None:
        buf = self.make()
        for i in range(2):
            buf.push(Item(i), 0.0)
        self.assertEqual(buf.clear(), 2)
        check_invariants(self, buf)

    def test_invalid_configuration(self) -> None:
        for args in ((0, 10, 1.0), (1, 0, 1.0), (1, 10, 0.0)):
            with self.assertRaises(InvalidConfigurationError):
                FrameRingBuffer(*args)

    def test_random_operations_keep_invariants(self) -> None:
        rng = random.Random(7)
        buf = self.make(capacity=4, max_bytes=45, stale=0.3)
        now = 0.0
        for i in range(2000):
            now += rng.random() * 0.1
            op = rng.choice(("push", "push", "latest", "oldest", "clear"))
            if op == "push":
                buf.push(Item(i, rng.choice((5, 10, 15))), now)
            elif op == "latest":
                buf.take_latest(now)
            elif op == "oldest":
                buf.take_oldest(now)
            else:
                buf.clear()
            check_invariants(self, buf)

    def test_thread_safety_under_concurrent_producers_and_consumer(self) -> None:
        buf = self.make(capacity=5, max_bytes=50, stale=10.0)
        stop = threading.Event()
        max_seen = []

        def produce(base: int) -> None:
            for i in range(3000):
                buf.push(Item(base + i), 0.0)

        def consume() -> None:
            while not stop.is_set():
                buf.take_latest(0.0)
                max_seen.append(len(buf))

        producers = [threading.Thread(target=produce, args=(k * 10_000,)) for k in range(4)]
        consumer = threading.Thread(target=consume)
        consumer.start()
        for t in producers:
            t.start()
        for t in producers:
            t.join()
        stop.set()
        consumer.join()
        self.assertLessEqual(max(max_seen), 5)
        self.assertEqual(buf.stats().received, 12_000)
        check_invariants(self, buf)


if __name__ == "__main__":
    unittest.main()
