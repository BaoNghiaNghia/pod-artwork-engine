from pathlib import Path

from pod_artwork_engine.contracts import JobRecord, JobState
from pod_artwork_engine.job_store import JobStore


def test_job_store_round_trip(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.save(JobRecord())
    loaded = store.get(job.job_id)
    assert loaded is not None
    assert loaded.job_id == job.job_id

    updated = store.transition(job.job_id, JobState.PREFLIGHT, progress=0.1)
    assert updated.state is JobState.PREFLIGHT
    assert updated.progress == 0.1
