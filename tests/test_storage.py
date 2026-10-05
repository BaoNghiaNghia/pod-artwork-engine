from pathlib import Path

from pod_artwork_engine.settings import Settings, StorageLimits
from pod_artwork_engine.storage import StorageManager


def test_storage_status_is_ok_for_empty_root(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path,
        storage=StorageLimits(
            soft_total_bytes=1000,
            hard_total_bytes=2000,
            cache_bytes=500,
            temp_jobs_bytes=500,
            logs_bytes=500,
            updates_bytes=500,
        ),
    )
    status = StorageManager(settings).status()
    assert status.state == "ok"
    assert status.used_bytes == 0


def test_update_cleanup_preserves_active_and_previous_releases(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path,
        storage=StorageLimits(
            soft_total_bytes=100_000,
            hard_total_bytes=200_000,
            cache_bytes=100_000,
            temp_jobs_bytes=100_000,
            logs_bytes=100_000,
            updates_bytes=30,
        ),
    )
    settings.ensure_directories()

    releases = settings.updates_dir / "releases"
    for version in ("1.0.0", "1.1.0", "0.9.0"):
        path = releases / version
        path.mkdir(parents=True, exist_ok=True)
        (path / "payload.bin").write_bytes(b"x" * 20)

    (settings.updates_dir / "update-state.json").write_text(
        '{"current_version":"1.1.0","previous_version":"1.0.0","staged_version":null}',
        encoding="utf-8",
    )

    StorageManager(settings)._cleanup_updates(30)

    assert (releases / "1.1.0").exists()
    assert (releases / "1.0.0").exists()
    assert not (releases / "0.9.0").exists()


def test_harness_cleanup_evicts_whole_old_runs(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path,
        storage=StorageLimits(
            soft_total_bytes=100_000,
            hard_total_bytes=200_000,
            cache_bytes=100_000,
            temp_jobs_bytes=100_000,
            logs_bytes=100_000,
            updates_bytes=100_000,
            harness_bytes=30,
        ),
    )
    settings.ensure_directories()

    old_run = settings.harness_dir / "runs" / "run-old"
    new_run = settings.harness_dir / "runs" / "run-new"
    old_run.mkdir(parents=True)
    new_run.mkdir(parents=True)
    (old_run / "scorecard.json").write_bytes(b"x" * 20)
    (new_run / "scorecard.json").write_bytes(b"y" * 20)

    import os
    import time

    old_time = time.time() - 60
    os.utime(old_run, (old_time, old_time))

    StorageManager(settings)._cleanup_harness(30)

    assert not old_run.exists()
    assert new_run.exists()
