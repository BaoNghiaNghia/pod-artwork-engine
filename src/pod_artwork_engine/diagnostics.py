from __future__ import annotations

import json
import platform
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import psutil

from . import __version__
from .hardware import detect_hardware
from .local_ocr import available as local_ocr_available
from .settings import Settings
from .storage import StorageManager
from .updater import UpdateManager


def build_diagnostic_bundle(settings: Settings, destination: Path | None = None) -> Path:
    settings.ensure_directories()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = destination or (settings.data_root / f"diagnostic_{timestamp}.zip")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        storage = StorageManager(settings).status()
        hardware = detect_hardware()
        system = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": __version__,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_logical": psutil.cpu_count(logical=True),
            "cpu_physical": psutil.cpu_count(logical=False),
            "memory_total": psutil.virtual_memory().total,
            "hardware": hardware.to_dict(),
            "storage": storage.model_dump(mode="json"),
        }
        (root / "system.json").write_text(json.dumps(system, indent=2), encoding="utf-8")

        config = {
            "data_root": str(settings.data_root),
            "host": settings.host,
            "port": settings.port,
            "cpu_soft_threads": settings.cpu_soft_threads,
            "ram_soft_bytes": settings.ram_soft_bytes,
            "ram_hard_bytes": settings.ram_hard_bytes,
            "release_channel": settings.release_channel,
            "release_manifest_configured": bool(settings.release_manifest_url),
            "remote_provider_configured": bool(settings.remote_provider_url),
            "remote_provider_name": settings.remote_provider_name,
            "provider_recipe_configured": settings.provider_recipe_path is not None,
            "qc_policy_configured": settings.qc_policy_path is not None,
            "qc_policy_path": (
                str(settings.qc_policy_path) if settings.qc_policy_path else ""
            ),
            "router_policy_configured": settings.router_policy_path is not None,
            "router_policy_path": (
                str(settings.router_policy_path) if settings.router_policy_path else ""
            ),
            "local_ocr_enabled": settings.local_ocr_enabled,
            "local_ocr_available": local_ocr_available(settings),
            "tesseract_language": settings.tesseract_language,
        }
        (root / "config-redacted.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

        update_state = UpdateManager(settings, __version__).state()
        (root / "update-state.json").write_text(
            json.dumps(update_state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        recent_logs = root / "logs"
        recent_logs.mkdir()
        for log_file in settings.logs_dir.rglob("*.log*"):
            try:
                relative = log_file.relative_to(settings.logs_dir)
                target = recent_logs / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(log_file, target)
            except OSError:
                pass

        for job_log in (settings.logs_dir / "jobs").glob("*.jsonl"):
            try:
                target = recent_logs / "jobs" / job_log.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(job_log, target)
            except OSError:
                pass

        archive_base = destination.with_suffix("")
        archive = shutil.make_archive(str(archive_base), "zip", root)
    return Path(archive)
