from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.core.config import Settings


_CONFIGURED = False
_RESERVED = set(logging.makeLogRecord({}).__dict__)


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }
        extra = {key: value for key, value in record.__dict__.items() if key not in _RESERVED and not key.startswith("_")}
        if extra:
            payload["extra"] = _json_safe(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(settings: Settings) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    log_dir = Path(settings.log_dir).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = StructuredFormatter()

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(_file_handler(log_dir / "app.log", formatter, level))

    worker_logger = logging.getLogger("worker")
    worker_logger.setLevel(level)
    worker_logger.addHandler(_file_handler(log_dir / "worker.log", formatter, level))
    worker_logger.propagate = True

    _CONFIGURED = True


def _file_handler(path: Path, formatter: logging.Formatter, level: int) -> RotatingFileHandler:
    handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except TypeError:
        return str(value)
