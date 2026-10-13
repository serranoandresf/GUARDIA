"""Frame: metadatos validados (Pydantic) + píxeles NumPy fuera de Pydantic, con invariantes comprobadas al construir."""
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import numpy.typing as npt
from pydantic import ValidationError

from app.video.domain.errors import InvalidFrameError
from app.video.domain.models import FrameGeometry, FrameMeta, summarize_validation_error

PixelArray = npt.NDArray[np.uint8]


@dataclass(frozen=True, slots=True, eq=False)
class Frame:
    """Los píxeles quedan de solo lectura: el productor cede la propiedad del buffer y nadie más puede mutarlo."""

    meta: FrameMeta
    pixels: PixelArray

    def __post_init__(self) -> None:
        pixels = self.pixels
        if not isinstance(pixels, np.ndarray) or pixels.dtype != np.uint8:
            raise InvalidFrameError("Los píxeles deben ser un ndarray uint8", camera_id=self.meta.camera_id)
        geo = self.meta.geometry
        if pixels.shape != (geo.height, geo.width, geo.channels):
            raise InvalidFrameError("La forma de los píxeles no coincide con la geometría", camera_id=self.meta.camera_id)
        pixels.flags.writeable = False

    @property
    def nbytes(self) -> int:
        return int(self.pixels.nbytes)

    @classmethod
    def from_array(cls, camera_id: str, sequence: int, captured_at: datetime, pixels: PixelArray) -> "Frame":
        if not isinstance(pixels, np.ndarray) or pixels.dtype != np.uint8 or pixels.ndim != 3:
            raise InvalidFrameError("Se esperaba un ndarray uint8 de forma (alto, ancho, canales)")
        height, width, channels = (int(n) for n in pixels.shape)
        try:
            meta = FrameMeta(
                camera_id=camera_id,
                sequence=sequence,
                captured_at=captured_at,
                geometry=FrameGeometry(width=width, height=height, channels=channels),
            )
        except ValidationError as exc:
            raise InvalidFrameError("Metadatos de frame inválidos: " + summarize_validation_error(exc)) from exc
        return cls(meta=meta, pixels=pixels)
