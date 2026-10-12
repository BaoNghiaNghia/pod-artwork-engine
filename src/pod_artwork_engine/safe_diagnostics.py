"""Local-only, sanitized diagnostic status for POD Studio support.

Do not include raw log lines, image names, prompts, exception text, provider
responses, API keys, local file paths, instance tokens, or manifest URLs.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import __version__
from .settings import Settings
from .system_monitor import log_summary, recent_safe_events
from .updater import UpdateManager

_SAFE_VERSION = re.compile(r"^[A-Za-z0-9._-]{1,40}$")


def _version(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_VERSION.fullmatch(value) else None


def _timestamp(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def safe_diagnostic_report(settings: Settings, *, recent_limit: int = 100) -> dict:
    manager = UpdateManager(settings, __version__)
    state = manager.state()
    logs = log_summary(settings)
    events = recent_safe_events(settings, limit=min(max(1, recent_limit), 100))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = []
    for event in events:
        try:
            stamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
            if stamp.tzinfo is not None and stamp >= cutoff:
                recent.append(event)
        except (KeyError, TypeError, ValueError):
            continue
    levels = Counter(str(e.get("level", "")).upper() for e in recent)
    failures = sum(
        e.get("event") in {"update_check_failed", "update_stage_failed", "api_unhandled_exception"}
        or str(e.get("stage", "")).startswith("http_5")
        for e in recent
    )
    # These counts are from a bounded sample, not exact totals for 24h.
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app_version": __version__,
        "update": {
            "current": _version(state.get("current_version")) or __version__,
            "previous": _version(state.get("previous_version")),
            "staged": _version(state.get("staged_version")),
            "last_success_at": _timestamp(state.get("last_success_at")),
            "last_failure_at": _timestamp(state.get("last_failure_at")),
            "has_last_error": bool(state.get("last_error")),
        },
        "logs": {
            "job_retention_days": logs["job_retention_days"],
            "job_log_count": logs["job_log_count"],
            "files": [{"name": f["name"], "present": f["present"], "bytes": f["bytes"]}
                      for f in logs["files"]],
        },
        "recent_event_sample": {
            "count": len(recent),
            "window_hours": 24,
            "max_scanned": min(max(1, recent_limit), 100),
            "error_or_failure_count": failures,
            "error_level_count": levels.get("ERROR", 0) + levels.get("CRITICAL", 0),
        },
        "privacy": "Sanitized metadata only. No raw logs, prompts, credentials or local paths.",
    }
