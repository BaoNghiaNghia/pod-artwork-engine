from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, HttpUrl

from .settings import Settings


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


def load_remote_manifest(url: str, timeout_seconds: int = 8) -> ReleaseManifest:
    request = urllib.request.Request(url, headers={"User-Agent": "PODArtworkTool-Updater/0.1"})
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


class UpdateManager:
    def __init__(self, settings: Settings, current_version: str) -> None:
        self.settings = settings
        self.current_version = current_version
        settings.ensure_directories()
        self.downloads_dir = settings.updates_dir / "downloads"
        self.releases_dir = settings.updates_dir / "releases"
        self.state_path = settings.updates_dir / "update-state.json"
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.releases_dir.mkdir(parents=True, exist_ok=True)

    def check(self) -> UpdateCheck:
        if not self.settings.release_manifest_url:
            return UpdateCheck(
                enabled=False,
                update_available=False,
                current_version=self.current_version,
                reason="release manifest URL not configured",
            )

        manifest = load_remote_manifest(self.settings.release_manifest_url)
        if manifest.channel != self.settings.release_channel:
            return UpdateCheck(
                enabled=True,
                update_available=False,
                current_version=self.current_version,
                latest_version=manifest.version,
                channel=manifest.channel,
                reason=f"manifest channel {manifest.channel!r} does not match configured channel",
            )

        return UpdateCheck(
            enabled=True,
            update_available=_is_newer(manifest.version, self.current_version),
            current_version=self.current_version,
            latest_version=manifest.version,
            channel=manifest.channel,
            reason="update available" if _is_newer(manifest.version, self.current_version) else "up to date",
        )

    def fetch_manifest(self) -> ReleaseManifest:
        if not self.settings.release_manifest_url:
            raise RuntimeError("release manifest URL not configured")
        manifest = load_remote_manifest(self.settings.release_manifest_url)
        if manifest.channel != self.settings.release_channel:
            raise RuntimeError(
                f"release channel mismatch: expected {self.settings.release_channel}, got {manifest.channel}"
            )
        return manifest

    def stage(self, manifest: ReleaseManifest | None = None) -> Path:
        manifest = manifest or self.fetch_manifest()
        package_path = self.downloads_dir / f"PODArtworkTool-{manifest.version}.zip"
        temp_path = package_path.with_suffix(".download")

        request = urllib.request.Request(
            str(manifest.package_url),
            headers={"User-Agent": "PODArtworkTool-Updater/0.1"},
        )
        with urllib.request.urlopen(request, timeout=30) as response, temp_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)

        if not verify_sha256(temp_path, manifest.sha256):
            temp_path.unlink(missing_ok=True)
            raise RuntimeError("release package SHA-256 verification failed")

        os.replace(temp_path, package_path)
        release_dir = self.releases_dir / manifest.version
        staging_dir = self.releases_dir / f".{manifest.version}.staging"
        shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)
        _safe_extract_zip(package_path, staging_dir)

        if release_dir.exists():
            shutil.rmtree(release_dir)
        os.replace(staging_dir, release_dir)

        self._write_state(
            {
                "current_version": self.current_version,
                "staged_version": manifest.version,
                "previous_version": self._read_state().get("current_version"),
                "channel": manifest.channel,
                "last_error": None,
            }
        )
        return release_dir

    def mark_active(self, version: str, previous_version: str | None = None) -> None:
        state = self._read_state()
        self._write_state(
            {
                "current_version": version,
                "staged_version": None,
                "previous_version": previous_version or state.get("current_version"),
                "channel": self.settings.release_channel,
                "last_error": None,
            }
        )
        self.prune_releases(keep={version, previous_version or ""})

    def mark_failed(self, error: str) -> None:
        state = self._read_state()
        state["last_error"] = error
        self._write_state(state)

    def rollback_target(self) -> Path | None:
        state = self._read_state()
        previous = state.get("previous_version")
        if not previous:
            return None
        candidate = self.releases_dir / previous
        return candidate if candidate.exists() else None

    def prune_releases(self, keep: set[str]) -> None:
        for candidate in self.releases_dir.iterdir():
            if not candidate.is_dir() or candidate.name.startswith("."):
                continue
            if candidate.name not in keep:
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
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            temp_path = Path(handle.name)
        os.replace(temp_path, self.state_path)
