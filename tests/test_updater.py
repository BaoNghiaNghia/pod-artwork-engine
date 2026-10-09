import hashlib
import zipfile
from pathlib import Path

import pytest

from pod_artwork_engine.settings import Settings, DEFAULT_RELEASE_MANIFEST_URL
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


def _add_ocr_runtime(path: Path) -> Path:
    runtime = path / "runtime" / "tesseract"
    (runtime / "tessdata").mkdir(parents=True, exist_ok=True)
    (runtime / "tesseract.exe").write_bytes(b"tesseract")
    (runtime / "tessdata" / "eng.traineddata").write_bytes(b"eng")
    return runtime


def test_bundled_runtime_uses_public_stable_manifest_by_default(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("POD_RELEASE_MANIFEST_URL", raising=False)
    assert Settings.from_env().release_manifest_url == DEFAULT_RELEASE_MANIFEST_URL
    monkeypatch.setenv("POD_RELEASE_MANIFEST_URL", "https://example.com/custom.json")
    assert Settings.from_env().release_manifest_url == "https://example.com/custom.json"


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


def test_release_validation_rejects_partial_bundled_ocr_runtime(tmp_path: Path) -> None:
    release = _make_release(tmp_path / "release")
    runtime = release / "runtime" / "tesseract"
    runtime.mkdir(parents=True)
    (runtime / "tesseract.exe").write_bytes(b"tesseract")

    valid, reason = validate_release_dir(release)
    assert valid is False
    assert "tessdata" in reason

    (runtime / "tessdata").mkdir()
    (runtime / "tessdata" / "eng.traineddata").write_bytes(b"eng")
    valid, reason = validate_release_dir(release)
    assert valid is True
    assert reason == "ok"


def test_seed_activate_and_rollback_state(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")
    manager = UpdateManager(settings, "1.0.0")
    installed = _make_release(tmp_path / "installed")
    _add_ocr_runtime(installed)

    seeded = manager.seed_current_release(installed, "1.0.0")
    assert seeded == settings.updates_dir / "releases" / "1.0.0"
    assert (seeded / "runtime" / "tesseract" / "tesseract.exe").is_file()
    assert (
        seeded / "runtime" / "tesseract" / "tessdata" / "eng.traineddata"
    ).is_file()
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

def test_new_bundled_version_is_staged_until_health_gated_activation(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")
    manager = UpdateManager(settings, "0.1.4")
    old_bundle = _make_release(tmp_path / "old_bundle")
    manager.seed_current_release(old_bundle, "0.1.0")

    new_bundle = _make_release(tmp_path / "new_bundle")
    config = new_bundle / "config" / "benchmark-recipe.local.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"recipe_id":"packaged-v1"}', encoding="utf-8")
    candidate = manager.seed_current_release(new_bundle, "0.1.4")

    assert manager.active_version() == "0.1.0"
    assert manager.state()["staged_version"] == "0.1.4"
    assert candidate == manager.releases_dir / "0.1.4"
    assert (candidate / "config" / "benchmark-recipe.local.json").read_text(
        encoding="utf-8"
    ) == '{"recipe_id":"packaged-v1"}'

    manager.mark_active("0.1.4")
    assert manager.active_version() == "0.1.4"
    assert manager.state()["staged_version"] is None
    assert manager.rollback_target() is not None


def test_bundled_seed_does_not_override_newer_staged_release(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")
    manager = UpdateManager(settings, "0.1.4")
    manager.seed_current_release(_make_release(tmp_path / "old"), "0.1.0")
    _make_release(manager.releases_dir / "0.1.5")
    state = manager.state()
    state["staged_version"] = "0.1.5"
    manager._write_state(state)

    manager.seed_current_release(_make_release(tmp_path / "bundled"), "0.1.4")
    assert manager.state()["staged_version"] == "0.1.5"
    assert manager.active_version() == "0.1.0"


def test_bundled_seed_never_downgrades_active_version(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")
    manager = UpdateManager(settings, "0.1.4")
    manager.seed_current_release(_make_release(tmp_path / "latest"), "0.1.5")
    manager.seed_current_release(_make_release(tmp_path / "old"), "0.1.4")

    assert manager.active_version() == "0.1.5"
    assert manager.state().get("staged_version") is None
