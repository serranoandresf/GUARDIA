import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app.video.domain.errors import BufferOverflowError, EventSerializationError, InvalidConfigurationError, WalError
from app.video.wal import SyncMode, WriteAheadLog, encode_record
from tests.support.simulators import FakeClock, flip_byte


class WalTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def wal(self, **kwargs: object) -> WriteAheadLog:
        w = WriteAheadLog(self.dir, **kwargs)  # type: ignore[arg-type]
        w.open()
        self.addCleanup(w.close)
        return w

    def ids(self, w: WriteAheadLog) -> list[str]:
        return [r.record_id for r in w.pending()]


class BasicTests(WalTestCase):
    def test_append_ack_and_idempotence(self) -> None:
        w = self.wal()
        self.assertTrue(w.append("a", b"1"))
        self.assertFalse(w.append("a", b"1"))
        self.assertTrue(w.append("b", b"2"))
        self.assertTrue(w.ack("a"))
        self.assertFalse(w.ack("a"))
        self.assertFalse(w.ack("desconocido"))
        self.assertFalse(w.append("a", b"1"))  # ya procesado: no se vuelve a encolar
        self.assertEqual([(r.record_id, r.payload) for r in w.pending()], [("b", b"2")])

    def test_state_survives_restart(self) -> None:
        w = self.wal()
        for i in range(5):
            w.append(f"e{i}", f"p{i}".encode())
        w.ack("e1"); w.ack("e3")
        w.close()
        w2 = WriteAheadLog(self.dir)
        report = w2.open()
        self.addCleanup(w2.close)
        self.assertEqual((report.pending, report.acked, report.duplicates, report.truncated_bytes), (3, 2, 0, 0))
        self.assertEqual([(r.record_id, r.payload) for r in w2.pending()], [("e0", b"p0"), ("e2", b"p2"), ("e4", b"p4")])
        self.assertFalse(w2.append("e1", b"otra vez"))

    def test_binary_payloads_and_records_have_utc_timestamps(self) -> None:
        w = self.wal()
        payload = bytes(range(256)) + b"\n\n" + b"GW"
        w.append("bin", payload)
        record = w.pending()[0]
        self.assertEqual(record.payload, payload)
        self.assertTrue(record.timestamp.endswith("+00:00"))

    def test_pending_limit_bounds_memory(self) -> None:
        w = self.wal()
        for i in range(10):
            w.append(f"e{i}", b"x")
        self.assertEqual(self.ids(w)[:3], ["e0", "e1", "e2"])
        self.assertEqual(len(w.pending(limit=4)), 4)

    def test_invalid_records_are_rejected(self) -> None:
        w = self.wal(max_payload_bytes=10)
        for rid, payload in (("", b"x"), ("con espacio", b"x"), ("a" * 129, b"x"), ("ok", b"x" * 11), ("ok", "texto")):
            with self.assertRaises(EventSerializationError):
                w.append(rid, payload)  # type: ignore[arg-type]

    def test_directory_lock_prevents_second_writer(self) -> None:
        self.wal()
        if os.name == "nt":
            self.skipTest("flock no disponible")
        with self.assertRaises(WalError):
            WriteAheadLog(self.dir).open()

    def test_must_be_open_and_invalid_config(self) -> None:
        w = WriteAheadLog(self.dir)
        with self.assertRaises(WalError):
            w.append("a", b"x")
        for kwargs in (dict(fsync_interval_s=0), dict(segment_max_bytes=10), dict(max_payload_bytes=0)):
            with self.assertRaises(InvalidConfigurationError):
                WriteAheadLog(self.dir, **kwargs)  # type: ignore[arg-type]

    def test_disk_budget_fails_closed(self) -> None:
        w = self.wal(segment_max_bytes=1024, max_total_bytes=2048)
        with self.assertRaises(BufferOverflowError):
            for i in range(100):
                w.append(f"e{i}", b"x" * 100)
        before = self.ids(w)
        self.assertGreater(len(before), 0)
        self.assertTrue(w.ack(before[0]))  # confirmar sigue permitido aunque esté lleno
        self.assertEqual(self.ids(w), before[1:])


class DurabilityModeTests(WalTestCase):
    def count_fsyncs(self, mode: SyncMode, appends: int, **kwargs: object) -> int:
        w = self.wal(sync_mode=mode, **kwargs)
        with mock.patch("os.fsync", wraps=os.fsync) as spy:
            for i in range(appends):
                w.append(f"e{i}", b"x")
            return spy.call_count

    def test_always_syncs_every_append(self) -> None:
        self.assertGreaterEqual(self.count_fsyncs(SyncMode.ALWAYS, 5), 5)

    def test_never_does_not_sync_on_append(self) -> None:
        self.assertEqual(self.count_fsyncs(SyncMode.NEVER, 5), 0)

    def test_interval_syncs_at_most_once_per_interval(self) -> None:
        clock = FakeClock(100.0)
        w = self.wal(sync_mode=SyncMode.INTERVAL, fsync_interval_s=1.0, monotonic=clock)
        with mock.patch("os.fsync", wraps=os.fsync) as spy:
            for i in range(5):
                w.append(f"a{i}", b"x")
            self.assertEqual(spy.call_count, 0)
            clock.advance(1.5)
            w.append("b", b"x")
            self.assertEqual(spy.call_count, 1)
            w.append("c", b"x")
            self.assertEqual(spy.call_count, 1)

    def test_close_always_syncs(self) -> None:
        w = self.wal(sync_mode=SyncMode.NEVER)
        w.append("a", b"x")
        with mock.patch("os.fsync", wraps=os.fsync) as spy:
            w.close()
        self.assertGreaterEqual(spy.call_count, 1)


class RotationCompactionTests(WalTestCase):
    def test_rotation_creates_segments_and_recovery_reads_all(self) -> None:
        w = self.wal(segment_max_bytes=1024)
        for i in range(30):
            w.append(f"e{i:02d}", b"x" * 100)
        self.assertGreater(w.stats().segments, 3)
        w.close()
        w2 = WriteAheadLog(self.dir, segment_max_bytes=1024)
        w2.open()
        self.addCleanup(w2.close)
        self.assertEqual(self.ids(w2), [f"e{i:02d}" for i in range(30)])

    def test_compaction_removes_dead_segments_and_keeps_pending(self) -> None:
        w = self.wal(segment_max_bytes=1024)
        for i in range(30):
            w.append(f"e{i:02d}", b"x" * 100)
        for i in range(25):
            w.ack(f"e{i:02d}")
        before = w.stats()
        report = w.compact()
        after = w.stats()
        self.assertGreater(report.deleted_segments, 0)
        self.assertLess(after.bytes_total, before.bytes_total)
        self.assertEqual(report.bytes_freed, before.bytes_total - after.bytes_total)
        self.assertEqual(self.ids(w), [f"e{i:02d}" for i in range(25, 30)])
        w.close()
        w2 = WriteAheadLog(self.dir, segment_max_bytes=1024)
        w2.open()
        self.addCleanup(w2.close)
        self.assertEqual(self.ids(w2), [f"e{i:02d}" for i in range(25, 30)])
        self.assertEqual(w2.compact().deleted_segments, 0)  # idempotente

    def test_compaction_never_loses_order(self) -> None:
        w = self.wal(segment_max_bytes=1024)
        for i in range(40):
            w.append(f"e{i:02d}", b"y" * 80)
        for i in range(0, 40, 2):
            w.ack(f"e{i:02d}")
        expected = [f"e{i:02d}" for i in range(1, 40, 2)]
        w.compact()
        self.assertEqual(self.ids(w), expected)
        w.append("nuevo", b"z")
        self.assertEqual(self.ids(w), expected + ["nuevo"])

    def test_leftover_tmp_files_are_removed_on_open(self) -> None:
        (self.dir / "wal-000000000001.log.tmp").write_bytes(b"basura")
        self.wal()
        self.assertEqual(list(self.dir.glob("*.tmp")), [])


class ConcurrencyTests(WalTestCase):
    def test_parallel_appends_lose_nothing(self) -> None:
        w = self.wal(sync_mode=SyncMode.NEVER, segment_max_bytes=64 * 1024)

        def worker(k: int) -> None:
            for i in range(200):
                w.append(f"t{k}-{i}", f"{k}:{i}".encode())

        threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(w.stats().pending, 1600)
        w.close()
        w2 = WriteAheadLog(self.dir, segment_max_bytes=64 * 1024)
        w2.open()
        self.addCleanup(w2.close)
        self.assertEqual({r.record_id for r in w2.pending()}, {f"t{k}-{i}" for k in range(8) for i in range(200)})

    def test_duplicate_appends_from_many_threads_write_once(self) -> None:
        w = self.wal(sync_mode=SyncMode.NEVER)
        results: list[bool] = []
        threads = [threading.Thread(target=lambda: results.append(w.append("mismo", b"x"))) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count(True), 1)
        self.assertEqual(w.stats().pending, 1)


class FormatTests(unittest.TestCase):
    def test_record_layout_is_stable(self) -> None:
        blob = encode_record("E", "a", "2026-10-12T15:30:00+00:00", b"hola")
        self.assertEqual(blob[:2], b"GW")
        self.assertEqual(int.from_bytes(blob[2:6], "big"), len(blob) - 10)
        self.assertIn(b'"v":1', blob)
        self.assertTrue(blob.endswith(b"hola"))


class SilentCorruptionTests(WalTestCase):
    def test_bit_flip_in_middle_loses_only_that_record(self) -> None:
        w = self.wal()
        for rid in ("a", "b", "c"):
            w.append(rid, rid.encode() * 50)
        w.close()
        path = sorted(self.dir.glob("wal-*.log"))[0]
        size_a = len(encode_record("E", "a", "2026-10-12T15:30:00+00:00", b"a" * 50))
        flip_byte(path, size_a + 40)  # dentro del registro "b"
        size_before = path.stat().st_size
        w2 = WriteAheadLog(self.dir)
        report = w2.open()
        self.addCleanup(w2.close)
        self.assertEqual(self.ids(w2), ["a", "c"])
        self.assertGreater(report.skipped_bytes, 0)
        self.assertEqual(report.truncated_bytes, 0)
        self.assertEqual(path.stat().st_size, size_before)  # no se destruyen registros válidos posteriores
        self.assertEqual(report.corrupt_segments, (1,))


if __name__ == "__main__":
    unittest.main()
