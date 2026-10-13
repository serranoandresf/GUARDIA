"""Registro estructurado (una línea JSON por evento). Todo texto pasa por `redact_secrets`."""
import json
import logging
from typing import Any

from app.video.domain.errors import redact_secrets


logging.getLogger("guardia").addHandler(logging.NullHandler())  # biblioteca: sin salida hasta que la aplicación configure el registro


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    if not logger.isEnabledFor(level):
        return
    safe = {k: redact_secrets(v) if isinstance(v, str) else v for k, v in fields.items()}
    logger.log(level, json.dumps({"event": event, **safe}, default=str, ensure_ascii=False, sort_keys=True))
