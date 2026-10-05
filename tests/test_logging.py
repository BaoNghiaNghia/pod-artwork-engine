import json
import logging
from pathlib import Path

from pod_artwork_engine.logging_config import LoggingRuntime, log_event


def test_job_log_is_written_separately(tmp_path: Path) -> None:
    runtime = LoggingRuntime(tmp_path)
    runtime.start()
    try:
        logger = logging.getLogger("test")
        log_event(logger, "job_test", job_id="job-123", trace_id="trace-1", stage="preflight")
    finally:
        runtime.stop()

    path = tmp_path / "jobs" / "job-123.jsonl"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["event"] == "job_test"
    assert payload["job_id"] == "job-123"
