from __future__ import annotations

import json
import platform
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import psutil

from .settings import Settings
from .storage import StorageManager


def build_diagnostic_bundle(settings: Settings, destination: Path | None = None) -> Path:
    settings.ensure_directories()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = destination or (settings.data_root / f"diagnostic_{timestamp}.zip")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        storage = StorageManager(settings).status()
        system = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_logical": psutil.cpu_count(logical=True),
            "cpu_physical": psutil.cpu_count(logical=False),
            "memory_total": psutil.virtual_memory().total,
            "storage": storage.model_dump(mode="json"),
        }
        (root / "system.json").write_text(json.dumps(system, indent=2), encoding="utf-8")

        recent_logs = root / "logs"
        recent_logs.mkdir()
        for log_file in settings.logs_dir.glob("*.log*"):
            try:
                shutil.copy2(log_file, recent_logs / log_file.name)
            except OSError:
                pass

        archive_base = destination.with_suffix("")
        archive = shutil.make_archive(str(archive_base), "zip", root)
    return Path(archive)
