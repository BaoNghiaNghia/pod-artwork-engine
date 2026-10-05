from __future__ import annotations

import json
import logging
import logging.handlers
import queue
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "job_id",
            "trace_id",
            "stage",
            "provider",
            "model_version",
            "duration_ms",
            "attempt",
            "failure_reason",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class LoggingRuntime:
    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=10000)
        self.listener: logging.handlers.QueueListener | None = None

    def start(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "engine.log",
            maxBytes=25 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(JsonFormatter())
        self.listener = logging.handlers.QueueListener(self.queue, handler)
        self.listener.start()

        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.handlers.clear()
        root.addHandler(logging.handlers.QueueHandler(self.queue))

    def stop(self) -> None:
        if self.listener:
            self.listener.stop()
            self.listener = None


def log_event(logger: logging.Logger, message: str, **fields: Any) -> None:
    logger.info(message, extra=fields)
