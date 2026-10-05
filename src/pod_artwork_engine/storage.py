from __future__ import annotations

import shutil
import time
from pathlib import Path

from .contracts import StorageStatus
from .settings import Settings


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


class StorageLimitExceeded(RuntimeError):
    pass


class StorageManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_directories()

    def status(self) -> StorageStatus:
        cache = directory_size(self.settings.cache_dir)
        jobs = directory_size(self.settings.jobs_dir)
        logs = directory_size(self.settings.logs_dir)
        updates = directory_size(self.settings.updates_dir)
        artifacts = directory_size(self.settings.artifacts_dir)
        used = cache + jobs + logs + updates + artifacts
        limits = self.settings.storage

        if used >= limits.hard_total_bytes:
            state = "hard_limit"
        elif used >= limits.soft_total_bytes:
            state = "soft_limit"
        else:
            state = "ok"

        return StorageStatus(
            used_bytes=used,
            soft_limit_bytes=limits.soft_total_bytes,
            hard_limit_bytes=limits.hard_total_bytes,
            cache_bytes=cache,
            jobs_bytes=jobs,
            logs_bytes=logs,
            updates_bytes=updates,
            state=state,
        )

    def assert_capacity(self, required_bytes: int = 0) -> None:
        current = self.status()
        if current.used_bytes + required_bytes > current.hard_limit_bytes:
            raise StorageLimitExceeded(
                f"Storage hard cap would be exceeded: "
                f"{current.used_bytes + required_bytes} > {current.hard_limit_bytes}"
            )

    def cleanup(self) -> dict[str, int]:
        before = self.status().used_bytes
        self._cleanup_directory(self.settings.cache_dir, self.settings.storage.cache_bytes)
        self._cleanup_directory(self.settings.updates_dir, self.settings.storage.updates_bytes)
        self._cleanup_completed_job_temp()
        after = self.status().used_bytes
        return {"before_bytes": before, "after_bytes": after, "freed_bytes": max(0, before - after)}

    @staticmethod
    def _cleanup_directory(path: Path, quota: int) -> None:
        if directory_size(path) <= quota:
            return
        files = [p for p in path.rglob("*") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime)
        while files and directory_size(path) > quota:
            candidate = files.pop(0)
            try:
                candidate.unlink()
            except OSError:
                pass

    def _cleanup_completed_job_temp(self) -> None:
        cutoff = time.time() - 24 * 60 * 60
        for temp_dir in self.settings.jobs_dir.glob("*/temp"):
            try:
                if temp_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except OSError:
                continue
