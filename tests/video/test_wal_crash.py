"""Pruebas de recuperación ante cortes de energía. Los cortes se simulan de forma determinista."""
import random
import shutil
import tempfile
import unittest
from pathlib import Path

from app.video.wal import SyncMode, WriteAheadLog, encode_record
from tests.support.simulators import last_segment, simulate_torn_append


class CrashTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "wal"
        self.addCleanup(self._tmp.cleanup)

    def reopen(self, **kwargs: object) -> tuple[WriteAheadLog, object]:
        w = WriteAheadLog(self.dir, **kwargs)  # type: ignore[arg-type]
        report = w.open()
        self.addCleanup(w.close)
        return w, report


class TornWriteTests(CrashTestCase):
    def test_cut_at_every_byte_of_the_last_record_never_corrupts_the_log(self) -> None:
        w = WriteAheadLog(self.dir)
        w.open()
        for rid in ("a", "b"):
            w.append(rid, rid.encode() * 30)
        size_before = w.stats().bytes_total
        w.append("c", b"c" * 30)
        size_after = w.stats().bytes_total
        w.close()
        record_len = size_after - size_before
        original = last_segment(self.dir).read_bytes()
        for removed in range(1, record_len):  # cada posible corte dentro del último registro
            shutil.rmtree(self.dir)
            self.dir.mkdir(parents=True)
            (self.dir / "wal-000000000001.log").write_bytes(original[: len(original) - removed])
            w2, report = self.reopen()
            self.assertEqual([r.record_id for r in w2.pending()], ["a", "b"], f"corte de {removed} bytes")
            self.assertEqual(report.truncated_bytes, record_len - removed)  # type: ignore[attr-defined]
            self.assertTrue(w2.append("d", b"d"))  # el log sigue siendo escribible y consistente
            w2.close()
            w3, _ = self.reopen()
            self.assertEqual([r.record_id for r in w3.pending()], ["a", "b", "d"])
            w3.close()

    def test_torn_tail_after_acks_keeps_acked_state(self) -> None:
        w = WriteAheadLog(self.dir)
        w.open()
        for rid in ("a", "b", "c"):
            w.append(rid, b"x")
        w.ack("a")
        w.close()
        simulate_torn_append(self.dir, random.Random(5))
        w2, _ = self.reopen()
        self.assertEqual([r.record_id for r in w2.pending()], ["b", "c"])


class RandomizedCrashTests(CrashTestCase):
    def test_confirmed_events_are_never_lost_across_random_crashes(self) -> None:
        for seed in range(40):
            shutil.rmtree(self.dir, ignore_errors=True)
            rng = random.Random(seed)
            expected: dict[str, bytes] = {}
            w, _ = self.reopen(segment_max_bytes=1024, sync_mode=SyncMode.ALWAYS)
            counter = 0
            for _ in range(rng.randint(20, 80)):
                op = rng.random()
                if op < 0.6 or not expected:
                    counter += 1
                    rid, payload = f"e{counter}", bytes(rng.randrange(256) for _ in range(rng.randint(0, 120)))
                    self.assertTrue(w.append(rid, payload))  # confirmado = durable
                    expected[rid] = payload
                elif op < 0.9:
                    rid = rng.choice(list(expected))
                    self.assertTrue(w.ack(rid))
                    del expected[rid]
                else:
                    w.compact()
            w.close()
            simulate_torn_append(self.dir, rng)  # corte de energía durante la escritura siguiente (nunca confirmada)
            w2, _ = self.reopen(segment_max_bytes=1024)
            recovered = {r.record_id: r.payload for r in w2.pending()}
            self.assertEqual(recovered, expected, f"semilla {seed}")
            self.assertEqual(list(recovered), list(expected))  # mismo orden de entrega
            w2.close()


class CompactionCrashTests(CrashTestCase):
    def test_crash_after_rename_before_deleting_old_segments_leaves_only_harmless_duplicates(self) -> None:
        w = WriteAheadLog(self.dir, segment_max_bytes=1024)
        w.open()
        for i in range(30):
            w.append(f"e{i:02d}", b"x" * 100)
        for i in range(0, 30, 3):
            w.ack(f"e{i:02d}")
        expected = [f"e{i:02d}" for i in range(30) if i % 3]
        snapshot = {p.name: p.read_bytes() for p in self.dir.glob("wal-*.log")}
        w.compact()
        w.close()
        survivors = {p.name for p in self.dir.glob("wal-*.log")}
        for name, data in snapshot.items():  # el corte dejó sin borrar los segmentos viejos
            if name not in survivors:
                (self.dir / name).write_bytes(data)
        w2, report = self.reopen(segment_max_bytes=1024)
        self.assertEqual([r.record_id for r in w2.pending()], expected)
        self.assertGreater(report.duplicates, 0)  # type: ignore[attr-defined]
        self.assertGreater(w2.compact().deleted_segments, 0)
        self.assertEqual([r.record_id for r in w2.pending()], expected)
        w2.close()
        w3, _ = self.reopen(segment_max_bytes=1024)
        self.assertEqual([r.record_id for r in w3.pending()], expected)

    def test_crash_before_rename_leaves_original_data_intact(self) -> None:
        w = WriteAheadLog(self.dir, segment_max_bytes=1024)
        w.open()
        for i in range(20):
            w.append(f"e{i:02d}", b"x" * 100)
        w.close()
        (self.dir / "wal-000000000001.log.tmp").write_bytes(encode_record("E", "fantasma", "2026-10-12T15:30:00+00:00", b"?")[:7])
        w2, _ = self.reopen(segment_max_bytes=1024)
        self.assertEqual([r.record_id for r in w2.pending()], [f"e{i:02d}" for i in range(20)])
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_acked_event_is_never_resurrected_by_compaction(self) -> None:
        w = WriteAheadLog(self.dir, segment_max_bytes=1024)
        w.open()
        for i in range(12):
            w.append(f"e{i:02d}", b"x" * 100)  # segmentos 1..N
        w.ack("e00")  # el ack queda en un segmento posterior al registro
        for i in range(12, 24):
            w.append(f"e{i:02d}", b"x" * 100)
        w.compact()
        w.close()
        w2, _ = self.reopen(segment_max_bytes=1024)
        self.assertNotIn("e00", [r.record_id for r in w2.pending()])


if __name__ == "__main__":
    unittest.main()
