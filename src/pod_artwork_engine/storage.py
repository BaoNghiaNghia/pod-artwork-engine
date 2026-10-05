from __future__ import annotations

import json
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
        datasets = directory_size(self.settings.datasets_dir)
        harness = directory_size(self.settings.harness_dir)
        used = cache + jobs + logs + updates + artifacts + datasets + harness
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
            artifacts_bytes=artifacts,
            datasets_bytes=datasets,
            harness_bytes=harness,
            state=state,
        )

    def assert_capacity(self, required_bytes: int = 0) -> None:
        current = self.status()
        if current.used_bytes + required_bytes > current.soft_limit_bytes:
            self.cleanup()
            current = self.status()
        if current.used_bytes + required_bytes > current.hard_limit_bytes:
            raise StorageLimitExceeded(
                f"Storage hard cap would be exceeded: "
                f"{current.used_bytes + required_bytes} > {current.hard_limit_bytes}"
            )

    def cleanup(self) -> dict[str, int]:
        before = self.status().used_bytes
        limits = self.settings.storage

        self._cleanup_directory(self.settings.cache_dir, limits.cache_bytes)
        self._cleanup_updates(limits.updates_bytes)
        self._cleanup_directory(self.settings.logs_dir, limits.logs_bytes)
        self._cleanup_harness(limits.harness_bytes)
        self._cleanup_stale_job_temp()
        self._cleanup_temp_quota(limits.temp_jobs_bytes)

        after = self.status().used_bytes
        return {
            "before_bytes": before,
            "after_bytes": after,
            "freed_bytes": max(0, before - after),
        }

    @staticmethod
    def _cleanup_directory(path: Path, quota: int) -> None:
        if directory_size(path) <= quota:
            return
        files = [item for item in path.rglob("*") if item.is_file()]
        files.sort(key=lambda item: item.stat().st_mtime)
        current = directory_size(path)
        for candidate in files:
            if current <= quota:
                break
            try:
                size = candidate.stat().st_size
                candidate.unlink()
                current = max(0, current - size)
            except OSError:
                continue

    def _cleanup_updates(self, quota: int) -> None:
        updates_dir = self.settings.updates_dir
        if directory_size(updates_dir) <= quota:
            return

        state_path = updates_dir / "update-state.json"
        protected_versions: set[str] = set()
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                for key in ("current_version", "previous_version", "staged_version"):
                    value = state.get(key)
                    if value:
                        protected_versions.add(str(value))
            except (OSError, json.JSONDecodeError):
                pass

        downloads = updates_dir / "downloads"
        if downloads.exists():
            packages = [item for item in downloads.iterdir() if item.is_file()]
            packages.sort(key=lambda item: item.stat().st_mtime)
            for candidate in packages:
                if directory_size(updates_dir) <= quota:
                    break
                try:
                    candidate.unlink()
                except OSError:
                    continue

        releases = updates_dir / "releases"
        if releases.exists() and directory_size(updates_dir) > quota:
            stale_releases = [
                item
                for item in releases.iterdir()
                if item.is_dir()
                and not item.name.startswith(".")
                and item.name not in protected_versions
            ]
            stale_releases.sort(key=lambda item: item.stat().st_mtime)
            for candidate in stale_releases:
                if directory_size(updates_dir) <= quota:
                    break
                shutil.rmtree(candidate, ignore_errors=True)

        for candidate in (releases.glob(".*.staging") if releases.exists() else []):
            try:
                if candidate.stat().st_mtime < time.time() - 24 * 60 * 60:
                    shutil.rmtree(candidate, ignore_errors=True)
            except OSError:
                continue

        for candidate in (releases.glob(".*.seed") if releases.exists() else []):
            try:
                if candidate.stat().st_mtime < time.time() - 24 * 60 * 60:
                    shutil.rmtree(candidate, ignore_errors=True)
            except OSError:
                continue

    def _cleanup_harness(self, quota: int) -> None:
        harness_dir = self.settings.harness_dir
        if directory_size(harness_dir) <= quota:
            return

        runs_dir = harness_dir / "runs"
        if not runs_dir.exists():
            self._cleanup_directory(harness_dir, quota)
            return

        runs = [item for item in runs_dir.iterdir() if item.is_dir()]
        runs.sort(key=lambda item: item.stat().st_mtime)
        for run_dir in runs:
            if directory_size(harness_dir) <= quota:
                break
            shutil.rmtree(run_dir, ignore_errors=True)

    def _cleanup_stale_job_temp(self) -> None:
        cutoff = time.time() - 24 * 60 * 60
        for temp_dir in self.settings.jobs_dir.glob("*/temp"):
            try:
                if temp_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except OSError:
                continue

    def _cleanup_temp_quota(self, quota: int) -> None:
        temp_files: list[Path] = []
        current = 0
        for temp_dir in self.settings.jobs_dir.glob("*/temp"):
            for item in temp_dir.rglob("*"):
                try:
                    if item.is_file():
                        current += item.stat().st_size
                        temp_files.append(item)
                except OSError:
                    continue

        if current <= quota:
            return

        temp_files.sort(key=lambda item: item.stat().st_mtime)
        protect_recent_after = time.time() - 60 * 60
        for candidate in temp_files:
            if current <= quota:
                break
            try:
                stat = candidate.stat()
                if stat.st_mtime >= protect_recent_after:
                    continue
                candidate.unlink()
                current = max(0, current - stat.st_size)
            except OSError:
                continue
