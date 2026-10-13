"""Write-Ahead Log append-only para alertas y eventos críticos. Solo biblioteca estándar.

CONTRATO DE DURABILIDAD
  * `append()` devuelve True/False solo DESPUÉS de cumplir el modo de sincronización:
      - SyncMode.ALWAYS  : fsync en cada escritura. Un registro confirmado sobrevive a un corte de energía
                           (salvo hardware o sistema de archivos que mienten sobre fsync).
      - SyncMode.INTERVAL: fsync como máximo cada `fsync_interval_s`. RIESGO RESIDUAL: los registros confirmados
                           en esa ventana pueden perderse en un corte de energía (no en un cierre normal ni en la caída del proceso).
      - SyncMode.NEVER   : solo `sync()` y `close()` fuerzan el disco. Pensado para pruebas.
  * Un registro parcialmente escrito (corte en medio de una escritura) se detecta por longitud y CRC32 y se descarta al abrir;
    nunca invalida los registros anteriores ni posteriores válidos.
  * Entrega al menos una vez: un evento no confirmado como procesado (`ack`) se vuelve a entregar tras un reinicio.
    El consumidor debe ser idempotente usando `record_id`. Exactamente una vez no se garantiza.
  * `append()` es idempotente por `record_id` (mientras esté pendiente o en la caché de confirmados recientes).
  * Riesgo residual no cubierto: corrupción silenciosa del medio con el mismo CRC, borrado externo de segmentos y disco lleno
    (se rechaza con BufferOverflowError antes de escribir; el sistema falla cerrado).

FORMATO: segmentos `wal-<12 dígitos>.log`; cada registro = b"GW" + longitud(4) + crc32(4) + cuerpo,
cuerpo = JSON de cabecera {v, k: E|A, id, ts UTC, n} + b"\\n" + payload. `A` es la marca atómica de "procesado".
"""
import json
import logging
import os
import re
import struct
import threading
import time
import zlib
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.video.domain.errors import BufferOverflowError, EventSerializationError, InvalidConfigurationError, WalError
from app.video.observability import log_event

logger = logging.getLogger("guardia.video.wal")

SCHEMA_VERSION = 1
_MAGIC = b"GW"
_HDR = struct.Struct(">2sII")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")
_SEG_RE = re.compile(r"^wal-(\d{12})\.log$")
_MAX_BODY = 16 * 1024 * 1024


class SyncMode(StrEnum):
    ALWAYS = "always"
    INTERVAL = "interval"
    NEVER = "never"


@dataclass(frozen=True, slots=True)
class WalRecord:
    record_id: str
    timestamp: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    segments: int
    records: int
    pending: int
    acked: int
    duplicates: int
    truncated_bytes: int  # cola parcial eliminada del último segmento
    skipped_bytes: int  # bytes ilegibles saltados en medio (corrupción, no cortes de energía)
    corrupt_segments: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CompactionReport:
    deleted_segments: int
    merged_segments: int
    bytes_freed: int


@dataclass(frozen=True, slots=True)
class WalStats:
    pending: int
    segments: int
    bytes_total: int
    active_segment: int
    appended: int
    acked: int


@dataclass(frozen=True, slots=True)
class _Rec:
    offset: int
    end: int
    kind: str
    record_id: str
    timestamp: str


def encode_record(kind: str, record_id: str, ts: str, payload: bytes) -> bytes:
    head = json.dumps({"v": SCHEMA_VERSION, "k": kind, "id": record_id, "ts": ts, "n": len(payload)}, separators=(",", ":"), sort_keys=True).encode()
    body = head + b"\n" + payload
    return _HDR.pack(_MAGIC, len(body), zlib.crc32(body) & 0xFFFFFFFF) + body


def _decode_at(data: bytes, pos: int) -> tuple[_Rec, bytes] | None:
    if pos + _HDR.size > len(data):
        return None
    magic, blen, crc = _HDR.unpack_from(data, pos)
    end = pos + _HDR.size + blen
    if magic != _MAGIC or blen > _MAX_BODY or end > len(data):
        return None
    body = data[pos + _HDR.size:end]
    if zlib.crc32(body) & 0xFFFFFFFF != crc:
        return None
    raw, sep, payload = body.partition(b"\n")
    if not sep:
        return None
    try:
        head: Any = json.loads(raw)
        ok = (isinstance(head, dict) and head.get("v") == SCHEMA_VERSION and head.get("k") in ("E", "A")
              and isinstance(head.get("id"), str) and _ID_RE.match(head["id"]) and isinstance(head.get("ts"), str) and head.get("n") == len(payload))
    except ValueError:
        return None
    if not ok:
        return None
    return _Rec(pos, end, head["k"], head["id"], head["ts"]), payload


def scan_segment(data: bytes) -> tuple[list[_Rec], int, int]:
    """Devuelve (registros válidos, fin del último válido, bytes ilegibles saltados en medio). Resincroniza tras daños."""
    recs: list[_Rec] = []
    pos = valid_end = skipped = 0
    while pos < len(data):
        decoded = _decode_at(data, pos)
        if decoded is not None:
            recs.append(decoded[0])
            pos = valid_end = decoded[0].end
            continue
        nxt = pos + 1
        while True:
            nxt = data.find(_MAGIC, nxt)
            if nxt == -1:
                return recs, valid_end, skipped
            if _decode_at(data, nxt) is not None:
                skipped += nxt - pos
                pos = nxt
                break
            nxt += 1
    return recs, valid_end, skipped


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WriteAheadLog:
    def __init__(
        self, directory: Path | str, *, sync_mode: SyncMode = SyncMode.ALWAYS, fsync_interval_s: float = 0.2,
        segment_max_bytes: int = 4 * 1024 * 1024, max_total_bytes: int = 256 * 1024 * 1024, max_payload_bytes: int = 1024 * 1024,
        acked_cache: int = 10_000, clock: Callable[[], datetime] = _utcnow, monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not (fsync_interval_s > 0 and segment_max_bytes >= 1024 and max_total_bytes >= segment_max_bytes and max_payload_bytes >= 1 and acked_cache >= 1):
            raise InvalidConfigurationError("Parámetros de WAL inválidos")
        self._dir = Path(directory)
        self._sync_mode, self._interval, self._seg_max, self._total_max = sync_mode, fsync_interval_s, segment_max_bytes, max_total_bytes
        self._payload_max, self._acked_max, self._clock, self._monotonic = max_payload_bytes, acked_cache, clock, monotonic
        self._lock = threading.RLock()
        self._fd: int | None = None
        self._lock_fd: int | None = None
        self._active = 1
        self._size = 0
        self._segments: dict[int, int] = {}
        self._pending: OrderedDict[str, tuple[int, int, int]] = OrderedDict()  # id -> (segmento, offset, tamaño)
        self._acked: OrderedDict[str, None] = OrderedDict()
        self._last_sync = 0.0
        self._appended = self._acked_total = 0
        self._open = False

    # ---------- ciclo de vida ----------
    def __enter__(self) -> "WriteAheadLog":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _path(self, seq: int) -> Path:
        return self._dir / f"wal-{seq:012d}.log"

    def _fsync_dir(self) -> None:
        if os.name == "nt":
            return
        fd = os.open(self._dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _acquire_dir_lock(self) -> None:
        try:
            import fcntl
        except ImportError:  # Windows: sin bloqueo de directorio
            return
        fd = os.open(self._dir / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise WalError("El WAL ya está abierto por otro proceso") from exc
        self._lock_fd = fd

    def open(self) -> RecoveryReport:
        with self._lock:
            if self._open:
                raise InvalidConfigurationError("El WAL ya está abierto")
            self._dir.mkdir(parents=True, exist_ok=True)
            self._acquire_dir_lock()
            try:
                report = self._recover()
            except BaseException:
                self._release_dir_lock()
                raise
            self._open = True
            log_event(logger, logging.INFO, "wal_recovered", **{k: getattr(report, k) for k in report.__slots__})
            return report

    def _recover(self) -> RecoveryReport:
        for tmp in self._dir.glob("*.tmp"):
            tmp.unlink()
        seqs = sorted(int(m.group(1)) for p in self._dir.iterdir() if (m := _SEG_RE.match(p.name)))
        records = duplicates = truncated = skipped_total = 0
        corrupt: list[int] = []
        acked_seen: OrderedDict[str, None] = OrderedDict()
        self._segments, self._pending = {}, OrderedDict()
        for idx, seq in enumerate(seqs):
            path = self._path(seq)
            data = path.read_bytes()
            recs, valid_end, skipped = scan_segment(data)
            if skipped:
                corrupt.append(seq)
                skipped_total += skipped
            if valid_end < len(data):
                tail = len(data) - valid_end
                if idx == len(seqs) - 1:  # cola parcial de la última escritura: se elimina
                    with open(path, "r+b") as fh:
                        fh.truncate(valid_end)
                        fh.flush()
                        os.fsync(fh.fileno())
                    truncated += tail
                    data = data[:valid_end]
                else:
                    corrupt.append(seq)
                    skipped_total += tail
            self._segments[seq] = len(data)
            for r in recs:
                records += 1
                if r.kind == "E":
                    if r.record_id in self._pending or r.record_id in acked_seen:
                        duplicates += 1
                    else:
                        self._pending[r.record_id] = (seq, r.offset, r.end - r.offset)
                else:
                    self._pending.pop(r.record_id, None)
                    acked_seen[r.record_id] = None
        self._acked = OrderedDict((k, None) for k in list(acked_seen)[-self._acked_max:])
        self._active = seqs[-1] if seqs else 1
        path = self._path(self._active)
        self._fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        self._size = os.fstat(self._fd).st_size
        self._segments[self._active] = self._size
        self._last_sync = self._monotonic()
        self._fsync_dir()
        return RecoveryReport(len(seqs), records, len(self._pending), len(acked_seen), duplicates, truncated, skipped_total, tuple(sorted(set(corrupt))))

    def _release_dir_lock(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)  # cerrar libera el flock
            self._lock_fd = None

    def close(self) -> None:
        with self._lock:
            if not self._open:
                return
            self.sync()
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            self._release_dir_lock()
            self._open = False

    def _require_open(self) -> None:
        if not self._open or self._fd is None:
            raise WalError("El WAL no está abierto")

    # ---------- escritura ----------
    def sync(self) -> None:
        with self._lock:
            if self._fd is not None:
                os.fsync(self._fd)
                self._last_sync = self._monotonic()

    def _maybe_sync(self) -> None:
        if self._sync_mode is SyncMode.ALWAYS or (self._sync_mode is SyncMode.INTERVAL and self._monotonic() - self._last_sync >= self._interval):
            self.sync()

    def _rotate(self) -> None:
        assert self._fd is not None
        os.fsync(self._fd)
        os.close(self._fd)
        self._active += 1
        self._fd = os.open(self._path(self._active), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL, 0o600)
        self._size = 0
        self._segments[self._active] = 0
        self._fsync_dir()
        self._last_sync = self._monotonic()

    def _write(self, rec: bytes) -> int:
        if self._size > 0 and self._size + len(rec) > self._seg_max:
            self._rotate()
        assert self._fd is not None
        offset, written, view = self._size, 0, memoryview(rec)
        try:
            while written < len(rec):
                written += os.write(self._fd, view[written:])
        except OSError as exc:
            try:
                os.ftruncate(self._fd, offset)  # no dejar un registro a medias
            except OSError:
                pass
            raise WalError(f"Fallo de escritura del WAL: {exc.strerror}") from exc
        self._size += len(rec)
        self._segments[self._active] = self._size
        self._maybe_sync()
        return offset

    def append(self, record_id: str, payload: bytes) -> bool:
        """True si se escribió; False si ya existía (idempotente). Falla cerrado si no cabe."""
        if not isinstance(record_id, str) or not _ID_RE.match(record_id) or not isinstance(payload, (bytes, bytearray)) or len(payload) > self._payload_max:
            raise EventSerializationError("Registro WAL inválido: id o payload fuera de contrato")
        with self._lock:
            self._require_open()
            if record_id in self._pending or record_id in self._acked:
                return False
            rec = encode_record("E", record_id, self._clock().isoformat(), bytes(payload))
            if sum(self._segments.values()) + len(rec) > self._total_max:
                raise BufferOverflowError("El WAL alcanzó su tamaño máximo", size=sum(self._segments.values()), max_bytes=self._total_max)
            offset = self._write(rec)
            self._pending[record_id] = (self._active, offset, len(rec))
            self._appended += 1
            return True

    def ack(self, record_id: str) -> bool:
        """Marca atómicamente como procesado. False si ya estaba confirmado o es desconocido."""
        with self._lock:
            self._require_open()
            if record_id not in self._pending:
                return False
            self._write(encode_record("A", record_id, self._clock().isoformat(), b""))  # si falla, el estado no cambia
            del self._pending[record_id]
            self._acked[record_id] = None
            while len(self._acked) > self._acked_max:
                self._acked.popitem(last=False)
            self._acked_total += 1
            return True

    # ---------- lectura ----------
    def _read_record(self, loc: tuple[int, int, int]) -> tuple[_Rec, bytes]:
        seg, offset, size = loc
        with open(self._path(seg), "rb") as fh:
            fh.seek(offset)
            data = fh.read(size)
        decoded = _decode_at(data, 0)
        if decoded is None:
            raise WalError("Registro del WAL ilegible tras la recuperación", segment=seg, offset=offset)
        return decoded

    def pending(self, limit: int | None = None) -> list[WalRecord]:
        """Registros pendientes en orden de escritura. `limit` acota la memoria usada."""
        with self._lock:
            self._require_open()
            out: list[WalRecord] = []
            for loc in self._pending.values():
                rec, payload = self._read_record(loc)
                out.append(WalRecord(rec.record_id, rec.timestamp, payload))
                if limit is not None and len(out) >= limit:
                    break
            return out

    def is_pending(self, record_id: str) -> bool:
        with self._lock:
            return record_id in self._pending

    def stats(self) -> WalStats:
        with self._lock:
            return WalStats(len(self._pending), len(self._segments), sum(self._segments.values()), self._active, self._appended, self._acked_total)

    # ---------- compactación ----------
    def _unlink_segment(self, seq: int) -> int:
        size = self._segments.pop(seq, 0)
        self._path(seq).unlink(missing_ok=True)
        return size

    def compact(self) -> CompactionReport:
        """Reescribe los segmentos cerrados con pendientes en uno solo (tmp + fsync + rename atómico) y luego borra los muertos.

        Orden seguro: primero se reescribe (los registros ya procesados desaparecen) y solo después se borran segmentos. Así un
        corte a mitad nunca "resucita" un evento ya confirmado: como mucho deja copias duplicadas que la recuperación ignora.
        """
        with self._lock:
            self._require_open()
            before_total = sum(self._segments.values())
            closed = sorted(s for s in self._segments if s != self._active)
            live: dict[int, list[str]] = {s: [] for s in closed}
            for rid, (seg, _, _) in self._pending.items():
                if seg in live:
                    live[seg].append(rid)
            dead = [s for s in closed if not live[s]]
            keep = [s for s in closed if live[s]]
            deleted = merged = 0
            if keep:
                live_bytes = sum(size for seg, _, size in self._pending.values() if seg in keep)
                total = sum(self._segments[s] for s in keep)
                if dead or len(keep) > 1 or total > 2 * live_bytes:
                    target, tmp = keep[0], self._dir / f"wal-{keep[0]:012d}.log.tmp"
                    order = [rid for rid, (seg, _, _) in self._pending.items() if seg in keep]
                    new_locs: dict[str, tuple[int, int, int]] = {}
                    pos = 0
                    with open(tmp, "wb") as fh:
                        for rid in order:
                            rec, payload = self._read_record(self._pending[rid])
                            blob = encode_record("E", rid, rec.timestamp, payload)
                            fh.write(blob)
                            new_locs[rid] = (target, pos, len(blob))
                            pos += len(blob)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, self._path(target))
                    self._fsync_dir()
                    for rid, loc in new_locs.items():
                        self._pending[rid] = loc
                    self._segments[target] = pos
                    for seg in keep[1:]:
                        self._unlink_segment(seg)
                        deleted += 1
                    merged = len(keep)
            for seg in dead:
                self._unlink_segment(seg)
                deleted += 1
            self._fsync_dir()
            freed = before_total - sum(self._segments.values())
            log_event(logger, logging.INFO, "wal_compacted", deleted=deleted, merged=merged, freed=freed)
            return CompactionReport(deleted, merged, freed)
