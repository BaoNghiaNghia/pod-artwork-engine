from __future__ import annotations

import json
import logging
import logging.handlers
import queue
import re
import time
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
            "cpu_percent",
            "process_rss_bytes",
            "memory_available_bytes",
            "storage_used_bytes",
            "quality_score",
            "route",
            "event",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


JOB_LOG_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
JOB_LOG_MAX_BYTES = 2 * 1024 * 1024
JOB_LOG_BACKUPS = 2
JOB_LOG_RETENTION_DAYS = 14


class PerJobJsonHandler(logging.Handler):
    def __init__(self, jobs_log_dir: Path) -> None:
        super().__init__()
        self.jobs_log_dir = jobs_log_dir
        self.jobs_log_dir.mkdir(parents=True, exist_ok=True)
        self.setFormatter(JsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        job_id = getattr(record, "job_id", None)
        if not isinstance(job_id, str) or not JOB_LOG_ID.fullmatch(job_id):
            return
        try:
            path = self.jobs_log_dir / f"{job_id}.jsonl"
            line = self.format(record) + "\n"
            with self.lock:
                if path.exists() and path.stat().st_size + len(line.encode("utf-8")) > JOB_LOG_MAX_BYTES:
                    for suffix in range(JOB_LOG_BACKUPS, 1, -1):
                        older = self.jobs_log_dir / f"{job_id}.jsonl.{suffix-1}"
                        newer = self.jobs_log_dir / f"{job_id}.jsonl.{suffix}"
                        if older.exists():
                            older.replace(newer)
                    path.replace(self.jobs_log_dir / f"{job_id}.jsonl.1")
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
        except Exception:
            self.handleError(record)


class LoggingRuntime:
    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=10000)
        self.listener: logging.handlers.QueueListener | None = None
        self._original_root_handlers: list[logging.Handler] | None = None
        self._original_root_level: int | None = None
        self._queue_handler: logging.Handler | None = None

    def start(self) -> None:
        if self.listener is not None:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._prune_old_job_logs()
        engine_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "engine.log",
            maxBytes=25 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        engine_handler.setFormatter(JsonFormatter())

        job_handler = PerJobJsonHandler(self.log_dir / "jobs")
        self.listener = logging.handlers.QueueListener(
            self.queue,
            engine_handler,
            job_handler,
            respect_handler_level=True,
        )
        self.listener.start()

        root = logging.getLogger()
        self._original_root_handlers = list(root.handlers)
        self._original_root_level = root.level
        self._queue_handler = logging.handlers.QueueHandler(self.queue)
        root.setLevel(logging.INFO)
        root.handlers.clear()
        root.addHandler(self._queue_handler)

    def _prune_old_job_logs(self) -> None:
        job_dir = self.log_dir / "jobs"
        if not job_dir.is_dir():
            return
        cutoff = time.time() - JOB_LOG_RETENTION_DAYS * 86400
        for path in job_dir.iterdir():
            if not path.is_file() or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}\.jsonl(?:\.[12])?", path.name):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass

    def stop(self) -> None:
        if self.listener:
            listener = self.listener
            listener.stop()
            self.listener = None
            for handler in listener.handlers:
                handler.close()
            root = logging.getLogger()
            if self._queue_handler in root.handlers:
                root.removeHandler(self._queue_handler)
            if self._original_root_handlers is not None:
                root.handlers[:] = self._original_root_handlers
                root.setLevel(self._original_root_level if self._original_root_level is not None else logging.WARNING)
            if self._queue_handler is not None:
                self._queue_handler.close()
            self._queue_handler = None


def log_event(logger: logging.Logger, message: str, **fields: Any) -> None:
    fields.setdefault("event", message)
    logger.info(message, extra=fields)
