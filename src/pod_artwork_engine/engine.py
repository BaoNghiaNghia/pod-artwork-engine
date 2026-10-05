from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from .contracts import FailureCategory, JobRecord, JobState, QualityMode
from .job_store import JobStore
from .logging_config import log_event
from .preflight import inspect_image
from .settings import Settings
from .storage import StorageManager


logger = logging.getLogger("pod_engine")


class Engine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_directories()
        self.storage = StorageManager(settings)
        self.jobs = JobStore(settings.database_path)

    def create_job(self, source_paths: list[Path], quality_mode: QualityMode) -> JobRecord:
        self.storage.assert_capacity()
        job = JobRecord(
            quality_mode=quality_mode,
            source_paths=[str(path) for path in source_paths],
        )
        job_dir = self.settings.jobs_dir / job.job_id
        (job_dir / "source").mkdir(parents=True, exist_ok=True)
        (job_dir / "checkpoints").mkdir(parents=True, exist_ok=True)
        (job_dir / "temp").mkdir(parents=True, exist_ok=True)

        copied_sources: list[str] = []
        for source in source_paths:
            target = job_dir / "source" / source.name
            shutil.copy2(source, target)
            copied_sources.append(str(target))
        job.source_paths = copied_sources
        self.jobs.save(job)
        log_event(logger, "job_created", job_id=job.job_id, trace_id=job.trace_id, stage="queued")
        return job

    def run_preflight(self, job_id: str) -> JobRecord:
        job = self.jobs.transition(job_id, JobState.PREFLIGHT, progress=0.05, message="Inspecting input")
        try:
            results = [inspect_image(Path(path)) for path in job.source_paths]
            checkpoint = self.settings.jobs_dir / job.job_id / "checkpoints" / "preflight.json"
            checkpoint.write_text(
                json.dumps([r.model_dump(mode="json") for r in results], indent=2),
                encoding="utf-8",
            )
            job = self.jobs.transition(
                job_id,
                JobState.WAITING_PROVIDER,
                progress=0.12,
                message="Pre-flight complete — reconstruction provider not configured yet",
            )
            log_event(
                logger,
                "preflight_completed",
                job_id=job.job_id,
                trace_id=job.trace_id,
                stage="preflight",
            )
            return job
        except Exception as exc:
            logger.exception(
                "preflight_failed",
                extra={"job_id": job.job_id, "trace_id": job.trace_id, "stage": "preflight"},
            )
            return self.jobs.transition(
                job_id,
                JobState.FAILED_FINAL,
                progress=0.05,
                message="Pre-flight failed",
                failure_category=FailureCategory.SOURCE_ERROR,
                failure_reason=str(exc),
            )
