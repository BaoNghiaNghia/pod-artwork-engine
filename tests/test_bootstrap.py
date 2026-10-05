from pathlib import Path

import pod_artwork_engine.bootstrap as bootstrap
from pod_artwork_engine.bootstrap import HealthResult
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.updater import UpdateManager


def _make_release(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pod-artwork-desktop.exe").write_bytes(b"desktop")
    (path / "pod-artwork-engine.exe").write_bytes(b"engine")
    return path


def test_health_gate_requires_launched_instance_token(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")

    class FakeProcess:
        def poll(self):
            return None

    seen = {}

    def fake_read_health(settings, timeout_seconds=1.0, expected_token=None):
        seen["token"] = expected_token
        return HealthResult(True, "1.0.0", "ok", expected_token)

    monkeypatch.setattr(bootstrap, "read_health", fake_read_health)
    result = bootstrap.wait_for_release_health(
        FakeProcess(),
        settings,
        "1.0.0",
        "launch-token",
        timeout_seconds=0.1,
    )

    assert result.healthy
    assert seen["token"] == "launch-token"


def test_bootstrap_launches_seeded_release(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")
    install = _make_release(tmp_path / "install")
    monkeypatch.setattr(bootstrap, "configure_bootstrap_logging", lambda settings: None)
    monkeypatch.setattr(
        bootstrap,
        "_launch_and_validate",
        lambda release_dir, version, settings, timeout: (
            object(),
            HealthResult(True, version, "ok"),
        ),
    )

    result = bootstrap.run_bootstrap(
        settings,
        install_dir=install,
        current_version="1.0.0",
        health_timeout_seconds=0.01,
    )

    assert result == 0
    assert UpdateManager(settings, "1.0.0").active_version() == "1.0.0"


def test_bootstrap_rolls_back_unhealthy_active_release(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")
    manager = UpdateManager(settings, "1.1.0")
    install = _make_release(tmp_path / "install")
    _make_release(manager.releases_dir / "1.0.0")
    _make_release(manager.releases_dir / "1.1.0")
    manager._write_state(
        {
            "current_version": "1.1.0",
            "previous_version": "1.0.0",
            "staged_version": None,
            "channel": "stable",
        }
    )

    monkeypatch.setattr(bootstrap, "configure_bootstrap_logging", lambda settings: None)

    def fake_launch(release_dir, version, settings, timeout):
        if version == "1.1.0":
            return None, HealthResult(False, None, "bad release")
        return object(), HealthResult(True, version, "ok")

    monkeypatch.setattr(bootstrap, "_launch_and_validate", fake_launch)

    result = bootstrap.run_bootstrap(
        settings,
        install_dir=install,
        current_version="1.1.0",
        health_timeout_seconds=0.01,
    )

    state = manager.state()
    assert result == 0
    assert state["current_version"] == "1.0.0"
    assert state["failed_version"] == "1.1.0"


def test_bootstrap_discards_bad_staged_release(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")
    manager = UpdateManager(settings, "1.0.0")
    install = _make_release(tmp_path / "install")
    manager.seed_current_release(install, "1.0.0")
    staged = _make_release(manager.releases_dir / "1.1.0")
    state = manager.state()
    state["staged_version"] = "1.1.0"
    manager._write_state(state)

    monkeypatch.setattr(bootstrap, "configure_bootstrap_logging", lambda settings: None)

    def fake_launch(release_dir, version, settings, timeout):
        if version == "1.1.0":
            return None, HealthResult(False, None, "bad staged release")
        return object(), HealthResult(True, version, "ok")

    monkeypatch.setattr(bootstrap, "_launch_and_validate", fake_launch)

    result = bootstrap.run_bootstrap(
        settings,
        install_dir=install,
        current_version="1.0.0",
        health_timeout_seconds=0.01,
    )

    state = manager.state()
    assert result == 0
    assert state["current_version"] == "1.0.0"
    assert state["staged_version"] is None
    assert state["failed_version"] == "1.1.0"
    assert not staged.exists()
