from pathlib import Path

from PIL import Image

from pod_artwork_engine.contracts import JobState, QualityMode
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.settings import Settings


def test_recovery_reuses_preflight_checkpoint(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGBA", (64, 64), (255, 255, 255, 255)).save(source)

    engine = Engine(Settings(data_root=tmp_path / "data"))
    job = engine.create_job([source], QualityMode.PRINT_READY)
    engine.run_preflight(job.job_id)
    engine.jobs.transition(job.job_id, JobState.ANALYZING, progress=0.2)

    recovered = engine.recover_interrupted_jobs()
    restored = next(item for item in recovered if item.job_id == job.job_id)

    assert restored.state is JobState.RESUMING
    assert restored.progress >= 0.12
