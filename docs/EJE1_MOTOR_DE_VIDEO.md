# Eje 1: motor de video, streams y recuperación ante desastres

## Mapa de módulos (`app/video/`)
| Módulo | Responsabilidad | Depende de Pydantic |
|---|---|---|
| `domain/limits.py`, `errors.py` | Límites duros y excepciones de dominio (con redacción de secretos) | No |
| `domain/types.py`, `models.py`, `frame.py` | Tipos, modelos inmutables estrictos y frames | Sí |
| `buffer.py` | Buffer circular por cámara (frames y bytes acotados, descarte de obsoletos) | No |
| `resilience.py` | Backoff exponencial con jitter y circuit breaker (CLOSED/OPEN/HALF_OPEN) | No |
| `ingest.py` | Un hilo de captura por cámara, supervisor asíncrono, watchdog, manager | No |
| `sources.py` | Fuentes reales: OpenCV (RTSP/MJPEG/USB/archivo) y foto fija | No (requiere OpenCV) |
| `wal.py` | Write-Ahead Log append-only con recuperación, rotación y compactación | No |

## Flujo
`Cámara → FrameSource (hilo) → frame_factory → FrameRingBuffer → next_frame() (consumidor asíncrono)`.
El consumidor siempre recibe el frame más reciente; los anteriores se cuentan como descartados. Los eventos críticos se escriben en el WAL
**antes** de procesarse y se marcan con `ack()` cuando terminan.

## Contrato de durabilidad del WAL
- **ALWAYS** (por defecto): `append()` vuelve solo después de `fsync`. Un evento confirmado sobrevive a un corte de energía.
- **INTERVAL**: fsync cada `fsync_interval_s`. Riesgo residual: los eventos confirmados dentro de esa ventana pueden perderse en un corte de energía.
- **NEVER**: solo `sync()` y `close()` fuerzan el disco (para pruebas).
- Un registro a medias se detecta (longitud y CRC32) y se descarta al arrancar sin dañar los demás; se verificó cortando el último registro en cada byte posible.
- Entrega **al menos una vez**: lo no confirmado con `ack()` se reentrega tras un reinicio; el consumidor debe ser idempotente por `record_id`.
- Compactación segura: primero se reescribe (tmp + fsync + rename atómico) y después se borran segmentos; un corte a mitad solo deja duplicados que la recuperación ignora, nunca resucita un evento confirmado.
- **No cubierto:** hardware que miente sobre fsync, corrupción silenciosa con CRC válido, borrado externo de segmentos. Disco lleno: se rechaza con `BufferOverflowError` antes de escribir.

## Límites de recursos (configurables)
Frames por buffer (1 a 64), memoria por stream (hasta 512 MiB), 4 KiB por evento, 1 hilo de captura por cámara (`max_cameras` = máximo de hilos),
timeouts de conexión y lectura (hasta 120 s), cola de reintentos acotada por el circuit breaker.

## Qué NO está verificado
`OpenCvSource` y `SnapshotSource` (necesitan OpenCV y cámaras reales), los modelos Pydantic y las pruebas de `tests/video/domain/` (no había
Pydantic ni pytest en el entorno de desarrollo; las ejecutará el CI), y `to_domain_metrics`.

## Aceptación
```bash
python -m pytest tests -q                      # en CI y localmente (requiere requirements-dev.txt)
python -m unittest tests.video.test_ring_buffer tests.video.test_resilience tests.video.test_wal \
  tests.video.test_wal_crash tests.video.test_ingest tests.video.test_ingest_stress      # sin dependencias extra
GUARDIA_STRESS=1 python -m unittest tests.video.test_ingest_stress                        # estrés de 20 s
```
