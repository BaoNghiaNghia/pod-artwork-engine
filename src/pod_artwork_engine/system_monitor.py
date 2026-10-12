"""Safe local diagnostics: metadata and allowlisted event fields only.

Never expose raw log lines to the desktop: exception text, file paths and
provider response bodies can contain secrets or user image prompts.
"""
from __future__ import annotations

import json
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .logging_config import JOB_LOG_RETENTION_DAYS
from .settings import Settings

LOG_NAMES = ("engine.log", "updater.log", "desktop-engine.log")
SAFE_FIELDS = ("timestamp", "level", "logger", "event", "stage", "route", "duration_ms", "job_id")
# Accept only bounded structured event fields; never render raw message/exception text.
_SAFE_EVENT = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_SAFE_STAGE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SAFE_ROUTE = re.compile(r"^/[a-zA-Z0-9/_{}.-]{1,120}$")
_SAFE_LOGGER = re.compile(r"^[a-zA-Z0-9_.]{1,80}$")
_SAFE_JOB_ID = re.compile(r"^[a-f0-9]{32}$")
_SAFE_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def log_summary(settings: Settings) -> dict:
    logs: list[dict] = []
    for name in LOG_NAMES:
        path = settings.logs_dir / name
        try:
            info = path.stat()
            logs.append({"name": name, "present": True, "bytes": info.st_size,
                         "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()})
        except OSError:
            logs.append({"name": name, "present": False, "bytes": 0, "modified_at": None})
    job_dir = settings.logs_dir / "jobs"
    try:
        job_count = sum(1 for path in job_dir.iterdir() if path.is_file() and path.name.endswith(".jsonl"))
    except OSError:
        job_count = 0
    return {"directory": str(settings.logs_dir), "rotation": "engine 25MB x 6; updater 5MB x 4; desktop 5MB x 2; job 2MB x 3",
            "job_retention_days": JOB_LOG_RETENTION_DAYS, "job_log_count": job_count, "files": logs}


def recent_safe_events(settings: Settings, limit: int = 40) -> list[dict]:
    path = settings.logs_dir / "engine.log"
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = deque(handle, maxlen=min(max(1, limit * 8), 800))
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or not record.get("event"):
                continue
            if not isinstance(record.get("event"), str) or not _SAFE_EVENT.fullmatch(record["event"]):
                continue
            safe = {"event": record["event"]}
            for key, pattern in (
                ("logger", _SAFE_LOGGER), ("stage", _SAFE_STAGE),
                ("route", _SAFE_ROUTE), ("job_id", _SAFE_JOB_ID),
            ):
                value = record.get(key)
                if isinstance(value, str) and pattern.fullmatch(value):
                    safe[key] = value
            level = record.get("level")
            if isinstance(level, str) and level in _SAFE_LEVELS:
                safe["level"] = level
            stamp = record.get("timestamp")
            if isinstance(stamp, str) and len(stamp) <= 40:
                try:
                    datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                    safe["timestamp"] = stamp
                except ValueError:
                    pass
            duration = record.get("duration_ms")
            if type(duration) in (int, float) and 0 <= duration < 1_000_000_000:
                safe["duration_ms"] = duration
            entries.append(safe)
        except (ValueError, TypeError):
            continue
    return entries[-min(max(1, limit), 100):]
