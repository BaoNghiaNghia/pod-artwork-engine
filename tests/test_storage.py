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
