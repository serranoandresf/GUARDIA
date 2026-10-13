"""Fuentes reales (OpenCV y foto fija por HTTP). Las credenciales viajan aparte de la URL y nunca se registran."""
import time
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import numpy as np
import numpy.typing as npt

from app.video.domain.errors import StreamConnectionError, StreamTimeoutError


@dataclass(frozen=True, slots=True)
class Credentials:
    username: str
    secret: str

    def __repr__(self) -> str:
        return "Credentials(username='***', secret='***')"


def url_with_credentials(url: str, credentials: Credentials | None) -> str:
    if credentials is None:
        return url
    parts = urlsplit(url)
    netloc = f"{quote(credentials.username, safe='')}:{quote(credentials.secret, safe='')}@{parts.netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class OpenCvSource:
    """RTSP, MJPEG, USB y archivos con cv2.VideoCapture. No verificada en CI: requiere OpenCV con FFmpeg."""

    def __init__(self, uri: str, *, kind: str, connect_timeout_s: float, read_timeout_s: float, credentials: Credentials | None = None) -> None:
        self._uri, self._kind, self._connect, self._read, self._credentials = uri, kind, connect_timeout_s, read_timeout_s, credentials
        self._cap: Any = None

    def open(self) -> None:
        try:
            import cv2
        except ImportError as exc:
            raise StreamConnectionError("OpenCV no está instalado") from exc
        target: Any = int(self._uri) if self._kind == "usb" else url_with_credentials(self._uri, self._credentials)
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(self._connect * 1000), cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self._read * 1000)]
        try:
            cap = cv2.VideoCapture(target, cv2.CAP_ANY, params)
        except TypeError:  # versiones antiguas sin parámetros de timeout
            cap = cv2.VideoCapture(target)
        if not cap.isOpened():
            cap.release()
            raise StreamConnectionError("No se pudo abrir la fuente de video")
        self._cap = cap

    def read(self) -> npt.NDArray[np.uint8]:
        if self._cap is None:
            raise StreamConnectionError("La fuente no está abierta")
        started = time.monotonic()
        ok, frame = self._cap.read()
        if not ok or frame is None:
            if time.monotonic() - started >= self._read * 0.9:
                raise StreamTimeoutError("Tiempo agotado leyendo el stream")
            raise StreamConnectionError("Lectura fallida o fin del stream")
        return frame  # type: ignore[no-any-return]

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


class SnapshotSource:
    """Foto fija por HTTP(S), una por lectura, con timeout."""

    def __init__(self, url: str, *, connect_timeout_s: float, read_timeout_s: float, credentials: Credentials | None = None, min_interval_s: float = 1.0) -> None:
        self._url, self._timeout, self._credentials, self._interval, self._last = url, max(connect_timeout_s, read_timeout_s), credentials, min_interval_s, 0.0

    def open(self) -> None:
        self.read()

    def read(self) -> npt.NDArray[np.uint8]:
        wait = self._last + self._interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            import cv2

            with urllib.request.urlopen(url_with_credentials(self._url, self._credentials), timeout=self._timeout) as resp:  # noqa: S310
                data = resp.read(16 * 1024 * 1024)
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        except TimeoutError as exc:
            raise StreamTimeoutError("Tiempo agotado leyendo la foto") from exc
        except Exception as exc:  # noqa: BLE001
            raise StreamConnectionError(f"No se pudo leer la foto: {type(exc).__name__}") from exc
        self._last = time.monotonic()
        if frame is None:
            raise StreamConnectionError("La respuesta no es una imagen válida")
        return frame  # type: ignore[no-any-return]

    def close(self) -> None:
        return None
