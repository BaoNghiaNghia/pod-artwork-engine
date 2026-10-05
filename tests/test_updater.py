import hashlib
import zipfile
from pathlib import Path

import pytest

from pod_artwork_engine.settings import Settings
from pod_artwork_engine.updater import (
    UpdateManager,
    _is_newer,
    _safe_extract_zip,
    validate_release_dir,
    verify_sha256,
)


def _make_release(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pod-artwork-desktop.exe").write_bytes(b"desktop")
    (path / "pod-artwork-engine.exe").write_bytes(b"engine")
    return path


def test_version_comparison() -> None:
    assert _is_newer("0.2.0", "0.1.9")
    assert not _is_newer("0.1.0", "0.1.0")


def test_sha256_verification(tmp_path: Path) -> None:
    path = tmp_path / "package.zip"
    path.write_bytes(b"pod")
    expected = hashlib.sha256(b"pod").hexdigest()
    assert verify_sha256(path, expected)


def test_zip_slip_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../escape.txt", "bad")

    with pytest.raises(ValueError):
        _safe_extract_zip(archive, tmp_path / "out")


def test_release_validation_requires_desktop_and_engine(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    valid, reason = validate_release_dir(release)
    assert not valid
    assert "pod-artwork-desktop.exe" in reason

    _make_release(release)
    valid, reason = validate_release_dir(release)
    assert valid
    assert reason == "ok"


def test_seed_activate_and_rollback_state(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")
    manager = UpdateManager(settings, "1.0.0")
    installed = _make_release(tmp_path / "installed")

    seeded = manager.seed_current_release(installed, "1.0.0")
    assert seeded == settings.updates_dir / "releases" / "1.0.0"
    assert manager.active_version() == "1.0.0"

    _make_release(settings.updates_dir / "releases" / "1.1.0")
    manager.mark_active("1.1.0")
    assert manager.active_version() == "1.1.0"

    rollback = manager.rollback_target()
    assert rollback is not None
    rollback_version, rollback_dir = rollback
    assert rollback_version == "1.0.0"
    assert rollback_dir.exists()

    manager.mark_rolled_back("1.1.0", "1.0.0", "health failed")
    assert manager.active_version() == "1.0.0"
    assert manager.state()["failed_version"] == "1.1.0"
    assert not (settings.updates_dir / "releases" / "1.1.0").exists()


def test_discard_staged_removes_failed_release(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")
    manager = UpdateManager(settings, "1.0.0")
    installed = _make_release(tmp_path / "installed")
    manager.seed_current_release(installed, "1.0.0")

    staged = _make_release(settings.updates_dir / "releases" / "1.1.0")
    state = manager.state()
    state["staged_version"] = "1.1.0"
    manager._write_state(state)

    manager.discard_staged("bad health")

    assert not staged.exists()
    assert manager.state()["staged_version"] is None
    assert manager.state()["failed_version"] == "1.1.0"
