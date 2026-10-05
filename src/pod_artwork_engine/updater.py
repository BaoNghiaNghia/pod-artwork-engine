from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, HttpUrl

from .settings import Settings


UPDATER_VERSION = "0.1.0"
DESKTOP_EXECUTABLE = "pod-artwork-desktop.exe"
ENGINE_EXECUTABLE = "pod-artwork-engine.exe"
REQUIRED_RELEASE_FILES = (DESKTOP_EXECUTABLE, ENGINE_EXECUTABLE)


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    channel: str
    package_url: HttpUrl
    sha256: str
    minimum_updater_version: str = "0.1.0"


@dataclass(frozen=True)
class UpdateCheck:
    enabled: bool
    update_available: bool
    current_version: str
    latest_version: str | None = None
    channel: str | None = None
    reason: str = ""


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_remote_manifest(url: str, timeout_seconds: int = 8) -> ReleaseManifest:
    request = urllib.request.Request(url, headers={"User-Agent": f"PODArtworkTool-Updater/{UPDATER_VERSION}"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return ReleaseManifest.model_validate(payload)


def verify_sha256(path: Path, expected_sha256: str) -> bool:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().lower() == expected_sha256.lower()


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            target = (destination / member.filename).resolve()
            if os.path.commonpath([destination, target]) != str(destination):
                raise ValueError(f"Unsafe package path: {member.filename}")
        package.extractall(destination)


def _is_newer(candidate: str, current: str) -> bool:
    try:
        return Version(candidate) > Version(current)
    except InvalidVersion:
        return candidate != current


def validate_release_dir(path: Path) -> tuple[bool, str]:
    if not path.is_dir():
        return False, f"release directory not found: {path}"
    missing = [name for name in REQUIRED_RELEASE_FILES if not (path / name).is_file()]
    if missing:
        return False, f"release is missing required files: {', '.join(missing)}"
    return True, "ok"


class UpdateManager:
    def __init__(
        self,
        settings: Settings,
        current_version: str,
        updater_version: str = UPDATER_VERSION,
    ) -> None:
        self.settings = settings
        self.current_version = current_version
        self.updater_version = updater_version
        settings.ensure_directories()
        self.downloads_dir = settings.updates_dir / "downloads"
        self.releases_dir = settings.updates_dir / "releases"
        self.state_path = settings.updates_dir / "update-state.json"
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.releases_dir.mkdir(parents=True, exist_ok=True)

    def state(self) -> dict:
        return self._read_state()

    def active_version(self) -> str:
        return str(self._read_state().get("current_version") or self.current_version)

    def current_release_dir(self) -> Path | None:
        version = self.active_version()
        candidate = self.releases_dir / version
        valid, _ = validate_release_dir(candidate)
        return candidate if valid else None

    def staged_release(self) -> tuple[str, Path] | None:
        state = self._read_state()
        version = state.get("staged_version")
        if not version:
            return None
        candidate = self.releases_dir / str(version)
        valid, _ = validate_release_dir(candidate)
        if not valid:
            return None
        return str(version), candidate

    def check(self) -> UpdateCheck:
        active_version = self.active_version()
        if not self.settings.release_manifest_url:
            return UpdateCheck(
                enabled=False,
                update_available=False,
                current_version=active_version,
                reason="release manifest URL not configured",
            )

        manifest = load_remote_manifest(self.settings.release_manifest_url)
        if manifest.channel != self.settings.release_channel:
            return UpdateCheck(
                enabled=True,
                update_available=False,
                current_version=active_version,
                latest_version=manifest.version,
                channel=manifest.channel,
                reason=f"manifest channel {manifest.channel!r} does not match configured channel",
            )
        if _is_newer(manifest.minimum_updater_version, self.updater_version):
            return UpdateCheck(
                enabled=True,
                update_available=False,
                current_version=active_version,
                latest_version=manifest.version,
                channel=manifest.channel,
                reason=(
                    f"bootstrap updater {self.updater_version} is older than required "
                    f"{manifest.minimum_updater_version}"
                ),
            )

        update_available = _is_newer(manifest.version, active_version)
        return UpdateCheck(
            enabled=True,
            update_available=update_available,
            current_version=active_version,
            latest_version=manifest.version,
            channel=manifest.channel,
            reason="update available" if update_available else "up to date",
        )

    def fetch_manifest(self) -> ReleaseManifest:
        if not self.settings.release_manifest_url:
            raise RuntimeError("release manifest URL not configured")
        manifest = load_remote_manifest(self.settings.release_manifest_url)
        if manifest.channel != self.settings.release_channel:
            raise RuntimeError(
                f"release channel mismatch: expected {self.settings.release_channel}, got {manifest.channel}"
            )
        if _is_newer(manifest.minimum_updater_version, self.updater_version):
            raise RuntimeError(
                f"release requires updater {manifest.minimum_updater_version}, "
                f"installed updater is {self.updater_version}"
            )
        return manifest

    def stage(self, manifest: ReleaseManifest | None = None) -> Path:
        manifest = manifest or self.fetch_manifest()
        package_path = self.downloads_dir / f"PODArtworkTool-{manifest.version}.zip"
        temp_path = package_path.with_suffix(".download")

        request = urllib.request.Request(
            str(manifest.package_url),
            headers={"User-Agent": f"PODArtworkTool-Updater/{self.updater_version}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response, temp_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)

            if not verify_sha256(temp_path, manifest.sha256):
                raise RuntimeError("release package SHA-256 verification failed")

            os.replace(temp_path, package_path)
            release_dir = self.releases_dir / manifest.version
            staging_dir = self.releases_dir / f".{manifest.version}.staging"
            shutil.rmtree(staging_dir, ignore_errors=True)
            staging_dir.mkdir(parents=True, exist_ok=True)
            _safe_extract_zip(package_path, staging_dir)

            valid, reason = validate_release_dir(staging_dir)
            if not valid:
                raise RuntimeError(reason)

            if release_dir.exists():
                shutil.rmtree(release_dir)
            os.replace(staging_dir, release_dir)

            state = self._read_state()
            state.update(
                {
                    "current_version": state.get("current_version") or self.current_version,
                    "staged_version": manifest.version,
                    "channel": manifest.channel,
                    "last_error": None,
                    "last_stage_at": utc_iso(),
                }
            )
            self._write_state(state)
            return release_dir
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            shutil.rmtree(self.releases_dir / f".{manifest.version}.staging", ignore_errors=True)
            self.mark_failed(str(exc), failed_version=manifest.version)
            raise

    def seed_current_release(self, source_dir: Path, version: str | None = None) -> Path:
        version = version or self.current_version
        target = self.releases_dir / version
        valid, _ = validate_release_dir(target)
        if not valid:
            source_valid, reason = validate_release_dir(source_dir)
            if not source_valid:
                raise RuntimeError(f"cannot seed installed release: {reason}")

            staging = self.releases_dir / f".{version}.seed"
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            for name in REQUIRED_RELEASE_FILES:
                shutil.copy2(source_dir / name, staging / name)

            if target.exists():
                shutil.rmtree(target)
            os.replace(staging, target)

        state = self._read_state()
        if not state.get("current_version"):
            state.update(
                {
                    "current_version": version,
                    "previous_version": None,
                    "staged_version": state.get("staged_version"),
                    "channel": self.settings.release_channel,
                    "last_error": state.get("last_error"),
                    "last_success_at": utc_iso(),
                }
            )
            self._write_state(state)
        return target

    def mark_active(self, version: str) -> None:
        candidate = self.releases_dir / version
        valid, reason = validate_release_dir(candidate)
        if not valid:
            raise RuntimeError(f"cannot activate invalid release: {reason}")

        state = self._read_state()
        old_current = str(state.get("current_version") or self.current_version)
        previous = old_current if old_current != version else state.get("previous_version")
        self._write_state(
            {
                **state,
                "current_version": version,
                "staged_version": None,
                "previous_version": previous,
                "channel": self.settings.release_channel,
                "last_error": None,
                "failed_version": None,
                "last_success_at": utc_iso(),
            }
        )
        self.prune_releases(keep={version, str(previous or "")})

    def discard_staged(self, error: str) -> None:
        state = self._read_state()
        staged = state.get("staged_version")
        if staged:
            shutil.rmtree(self.releases_dir / str(staged), ignore_errors=True)
        state.update(
            {
                "staged_version": None,
                "failed_version": staged,
                "last_error": error,
                "last_failure_at": utc_iso(),
            }
        )
        self._write_state(state)

    def mark_rolled_back(self, failed_version: str, rollback_version: str, error: str) -> None:
        rollback_dir = self.releases_dir / rollback_version
        valid, reason = validate_release_dir(rollback_dir)
        if not valid:
            raise RuntimeError(f"rollback release is invalid: {reason}")
        state = self._read_state()
        self._write_state(
            {
                **state,
                "current_version": rollback_version,
                "previous_version": None,
                "staged_version": None,
                "failed_version": failed_version,
                "last_error": error,
                "last_failure_at": utc_iso(),
                "last_success_at": utc_iso(),
            }
        )
        self.prune_releases(keep={rollback_version})

    def mark_failed(self, error: str, failed_version: str | None = None) -> None:
        state = self._read_state()
        state.update(
            {
                "last_error": error,
                "failed_version": failed_version or state.get("failed_version"),
                "last_failure_at": utc_iso(),
            }
        )
        self._write_state(state)

    def rollback_target(self) -> tuple[str, Path] | None:
        state = self._read_state()
        previous = state.get("previous_version")
        if not previous:
            return None
        candidate = self.releases_dir / str(previous)
        valid, _ = validate_release_dir(candidate)
        return (str(previous), candidate) if valid else None

    def prune_releases(self, keep: set[str]) -> None:
        normalized = {item for item in keep if item}
        for candidate in self.releases_dir.iterdir():
            if not candidate.is_dir() or candidate.name.startswith("."):
                continue
            if candidate.name not in normalized:
                shutil.rmtree(candidate, ignore_errors=True)

    def _read_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_state(self, payload: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=self.state_path.parent,
            prefix=".update-state.",
            suffix=".tmp",
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            temp_path = Path(handle.name)
        os.replace(temp_path, self.state_path)
