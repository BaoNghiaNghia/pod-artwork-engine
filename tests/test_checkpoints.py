from pathlib import Path

from pod_artwork_engine.checkpoints import CheckpointManager


def test_checkpoint_round_trip(tmp_path: Path) -> None:
    manager = CheckpointManager(tmp_path)
    manager.write("job-1", "preflight", {"ok": True, "items": [1, 2]})

    assert manager.exists("job-1", "preflight")
    assert manager.payload("job-1", "preflight") == {"ok": True, "items": [1, 2]}
