"""Buffer circular por cámara: límites estrictos de frames y bytes, descarte de lo obsoleto, nunca crece.

Productor (hilo de captura) y consumidor (event loop) comparten el buffer: todas las operaciones son seguras entre hilos.
Invariante verificada en pruebas: recibidos = consumidos + descartados + presentes.
"""
import threading
from collections import deque
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from app.video.domain.errors import BufferOverflowError, InvalidConfigurationError


class SizedItem(Protocol):
    @property
    def nbytes(self) -> int: ...


T = TypeVar("T", bound=SizedItem)


@dataclass(frozen=True, slots=True)
class BufferStats:
    received: int
    consumed: int
    dropped_overflow: int  # expulsados por capacidad o memoria al llegar uno nuevo
    dropped_stale: int  # caducados por antigüedad
    dropped_superseded: int  # descartados al entregar uno más reciente
    size: int
    capacity: int
    bytes_used: int
    max_bytes: int

    @property
    def dropped_total(self) -> int:
        return self.dropped_overflow + self.dropped_stale + self.dropped_superseded


class FrameRingBuffer(Generic[T]):
    def __init__(self, capacity: int, max_bytes: int, stale_after_s: float, camera_id: str | None = None) -> None:
        if capacity < 1 or max_bytes < 1 or stale_after_s <= 0:
            raise InvalidConfigurationError("capacity, max_bytes y stale_after_s deben ser positivos", camera_id=camera_id)
        self._capacity, self._max_bytes, self._stale_after_s, self._camera_id = capacity, max_bytes, stale_after_s, camera_id
        self._items: deque[tuple[float, T, int]] = deque()
        self._lock = threading.Lock()
        self._bytes = 0
        self._received = self._consumed = self._d_overflow = self._d_stale = self._d_superseded = 0

    def push(self, item: T, now: float) -> int:
        """Agrega un elemento y devuelve cuántos antiguos se expulsaron. No bloquea ni crece más allá de los límites."""
        size = int(item.nbytes)
        if size > self._max_bytes:
            raise BufferOverflowError(
                "El elemento no cabe en el presupuesto de memoria del buffer", camera_id=self._camera_id, size=size, max_bytes=self._max_bytes
            )
        with self._lock:
            self._received += 1
            evicted = 0
            while self._items and (len(self._items) >= self._capacity or self._bytes + size > self._max_bytes):
                _, _, old = self._items.popleft()
                self._bytes -= old
                self._d_overflow += 1
                evicted += 1
            self._items.append((now, item, size))
            self._bytes += size
            return evicted

    def _expire(self, now: float) -> None:
        while self._items and now - self._items[0][0] > self._stale_after_s:
            _, _, old = self._items.popleft()
            self._bytes -= old
            self._d_stale += 1

    def take_latest(self, now: float) -> T | None:
        """Entrega el más reciente no caducado y descarta los anteriores (ya son obsoletos)."""
        with self._lock:
            self._expire(now)
            if not self._items:
                return None
            _, item, _ = self._items.pop()
            self._d_superseded += len(self._items)
            self._items.clear()
            self._bytes = 0
            self._consumed += 1
            return item

    def take_oldest(self, now: float) -> T | None:
        """Entrega el más antiguo no caducado (consumidores que necesitan orden)."""
        with self._lock:
            self._expire(now)
            if not self._items:
                return None
            _, item, size = self._items.popleft()
            self._bytes -= size
            self._consumed += 1
            return item

    def clear(self) -> int:
        with self._lock:
            n = len(self._items)
            self._d_overflow += n
            self._items.clear()
            self._bytes = 0
            return n

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def stats(self) -> BufferStats:
        with self._lock:
            return BufferStats(
                self._received, self._consumed, self._d_overflow, self._d_stale, self._d_superseded,
                len(self._items), self._capacity, self._bytes, self._max_bytes,
            )
