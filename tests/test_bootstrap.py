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

def test_bootstrap_promotes_newer_bundled_release_after_health_pass(
    monkeypatch, tmp_path: Path
) -> None:
    settings = Settings(data_root=tmp_path / "data")
    manager = UpdateManager(settings, "0.1.4")
    manager.seed_current_release(_make_release(tmp_path / "old"), "0.1.0")
    install = _make_release(tmp_path / "install")
    launched = []
    monkeypatch.setattr(bootstrap, "configure_bootstrap_logging", lambda settings: None)
    monkeypatch.setattr(bootstrap, "reclaim_orphaned_managed_engine", lambda settings: [])
    def launch(path, version, settings, timeout):
        launched.append(version)
        return object(), HealthResult(True, version, "ok")

    monkeypatch.setattr(bootstrap, "_launch_and_validate", launch)
    result = bootstrap.run_bootstrap(
        settings, install_dir=install, current_version="0.1.4",
        health_timeout_seconds=0.01
    )
    assert result == 0
    assert launched == ["0.1.4"]
    assert manager.active_version() == "0.1.4"
    assert manager.rollback_target()[0] == "0.1.0"


def test_reclaim_orphaned_managed_engine_ignores_other_app_and_running_desktop(
    monkeypatch, tmp_path: Path
) -> None:
    import psutil
    settings = Settings(data_root=tmp_path / "data")

    class Listener:
        status = psutil.CONN_LISTEN
        laddr = type("Addr", (), {"port": settings.port})()

    class FakeProcess:
        def __init__(self, pid, name, executable):
            self.pid = pid
            self.info = {"name": name, "exe": str(executable)}
            self.terminated = False
        def net_connections(self, **kwargs):
            return [Listener()]
        def terminate(self):
            self.terminated = True
        def wait(self, timeout):
            return 0

    owned = FakeProcess(10, "pod-artwork-engine.exe",
        settings.updates_dir / "releases" / "0.1.0" / "pod-artwork-engine.exe")
    other = FakeProcess(11, "pod-artwork-engine.exe",
        tmp_path / "unrelated" / "pod-artwork-engine.exe")
    desktop = FakeProcess(12, "pod-artwork-desktop.exe",
        settings.updates_dir / "releases" / "0.1.0" / "pod-artwork-desktop.exe")

    monkeypatch.setattr(bootstrap.psutil, "process_iter",
                        lambda _attrs: [owned, other, desktop])
    assert bootstrap.reclaim_orphaned_managed_engine(settings) == []
    assert not owned.terminated
    assert not other.terminated

    monkeypatch.setattr(bootstrap.psutil, "process_iter",
                        lambda _attrs: [owned, other])
    assert bootstrap.reclaim_orphaned_managed_engine(settings) == [10]
    assert owned.terminated
    assert not other.terminated


def test_bootstrap_reclaims_orphan_when_relaunching_active_version(
    monkeypatch, tmp_path: Path
) -> None:
    settings = Settings(data_root=tmp_path / "data")
    manager = UpdateManager(settings, "1.0.0")
    install = _make_release(tmp_path / "install")
    manager.seed_current_release(install, "1.0.0")

    calls: list[str] = []
    monkeypatch.setattr(bootstrap, "configure_bootstrap_logging", lambda settings: None)
    monkeypatch.setattr(
        bootstrap,
        "reclaim_orphaned_managed_engine",
        lambda settings: calls.append("cleanup") or [],
    )
    monkeypatch.setattr(
        bootstrap,
        "_launch_and_validate",
        lambda _path, version, _settings, _timeout: (
            object(), HealthResult(True, version, "ok")
        ),
    )

    result = bootstrap.run_bootstrap(
        settings,
        install_dir=install,
        current_version="1.0.0",
        health_timeout_seconds=0.01,
    )
    assert result == 0
    assert calls == ["cleanup"]
    assert manager.active_version() == "1.0.0"


def test_bootstrap_does_not_launch_duplicate_managed_desktop(
    monkeypatch, tmp_path: Path
) -> None:
    settings = Settings(data_root=tmp_path / "data")
    install = _make_release(tmp_path / "install")
    monkeypatch.setattr(bootstrap, "configure_bootstrap_logging", lambda settings: None)
    monkeypatch.setattr(bootstrap, "desktop_process_running", lambda settings: True)

    def unexpected(*args, **kwargs):
        raise AssertionError("Must not launch duplicate desktop while one is running")

    monkeypatch.setattr(bootstrap, "_launch_and_validate", unexpected)
    assert bootstrap.run_bootstrap(
        settings,
        install_dir=install,
        current_version="1.0.1",
        health_timeout_seconds=0.01,
    ) == 0
    assert UpdateManager(settings, "1.0.1").active_version() == "1.0.1"


def test_managed_desktop_detection_ignores_unrelated_processes(
    monkeypatch, tmp_path: Path
) -> None:
    settings = Settings(data_root=tmp_path / "data")

    class FakeProcess:
        def __init__(self, name: str, exe: Path):
            self.info = {"name": name, "exe": str(exe)}

    managed = FakeProcess(
        "pod-artwork-desktop.exe",
        settings.updates_dir / "releases" / "1.0.1" / "pod-artwork-desktop.exe",
    )
    unrelated = FakeProcess(
        "pod-artwork-desktop.exe",
        tmp_path / "other" / "pod-artwork-desktop.exe",
    )
    monkeypatch.setattr(
        bootstrap.psutil,
        "process_iter",
        lambda _attrs: [unrelated],
    )
    assert bootstrap.desktop_process_running(settings) is False
    monkeypatch.setattr(
        bootstrap.psutil,
        "process_iter",
        lambda _attrs: [unrelated, managed],
    )
    assert bootstrap.desktop_process_running(settings) is True
