"""Límites duros del dominio de video.

Son contratos de recursos: cualquier cambio exige revisar la memoria máxima por stream y las pruebas asociadas.
"""
from datetime import datetime, timezone
from typing import Final

MAX_SEQUENCE: Final[int] = 2**63 - 1

MIN_FRAME_DIMENSION: Final[int] = 16
MAX_FRAME_WIDTH: Final[int] = 7680
MAX_FRAME_HEIGHT: Final[int] = 4320
MAX_FRAME_CHANNELS: Final[int] = 4
MAX_FRAME_BYTES: Final[int] = 64 * 1024 * 1024  # un frame no puede ocupar más de 64 MiB (un 8K de 3 canales no cabe)

MAX_FPS: Final[float] = 240.0
MAX_TIMEOUT_SECONDS: Final[float] = 120.0
MIN_STALE_MS: Final[int] = 50
MAX_STALE_MS: Final[int] = 60_000

MAX_BUFFER_CAPACITY: Final[int] = 64
MAX_STREAM_MEMORY_BYTES: Final[int] = 512 * 1024 * 1024  # capacidad del buffer × frame máximo, por cámara

MAX_EVENT_PAYLOAD_BYTES: Final[int] = 4096  # un evento lleva metadatos ligeros, nunca imágenes
MAX_EVENT_ATTRIBUTES: Final[int] = 16
MAX_ATTRIBUTE_VALUE_CHARS: Final[int] = 256

MAX_RECENT_ERRORS: Final[int] = 10
MAX_ERROR_TEXT_CHARS: Final[int] = 200
MAX_LATENCY_MS: Final[float] = 3_600_000.0

MIN_TIMESTAMP: Final[datetime] = datetime(2020, 1, 1, tzinfo=timezone.utc)
MAX_TIMESTAMP: Final[datetime] = datetime(2100, 1, 1, tzinfo=timezone.utc)
