from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from .checkpoints import CheckpointManager
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
        self.checkpoints = CheckpointManager(settings.jobs_dir)

    def create_job(self, source_paths: list[Path], quality_mode: QualityMode) -> JobRecord:
        required = sum(path.stat().st_size for path in source_paths if path.exists())
        self.storage.assert_capacity(required)
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
        existing = self.checkpoints.payload(job_id, "preflight")
        if existing is not None:
            job = self.jobs.transition(
                job_id,
                JobState.WAITING_PROVIDER,
                progress=0.12,
                message="Pre-flight restored from checkpoint",
            )
            log_event(
                logger,
                "preflight_checkpoint_reused",
                job_id=job.job_id,
                trace_id=job.trace_id,
                stage="preflight",
            )
            return job

        job = self.jobs.transition(job_id, JobState.PREFLIGHT, progress=0.05, message="Inspecting input")
        started = time.perf_counter()
        try:
            results = [inspect_image(Path(path)) for path in job.source_paths]
            self.checkpoints.write(
                job.job_id,
                "preflight",
                [result.model_dump(mode="json") for result in results],
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
                duration_ms=round((time.perf_counter() - started) * 1000),
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

    def recover_interrupted_jobs(self) -> list[JobRecord]:
        recovered: list[JobRecord] = []
        for job in self.jobs.list_interrupted():
            if job.state in {JobState.WAITING_PROVIDER, JobState.BLOCKED_BUDGET}:
                continue
            if self.checkpoints.exists(job.job_id, "preflight"):
                recovered_job = self.jobs.transition(
                    job.job_id,
                    JobState.WAITING_PROVIDER,
                    progress=max(job.progress, 0.12),
                    message="Recovered after restart — waiting for reconstruction provider",
                )
            else:
                recovered_job = self.jobs.transition(
                    job.job_id,
                    JobState.RESUMING,
                    progress=min(job.progress, 0.05),
                    message="Recovered after restart — resuming pre-flight",
                )
            recovered.append(recovered_job)
            log_event(
                logger,
                "job_recovered",
                job_id=recovered_job.job_id,
                trace_id=recovered_job.trace_id,
                stage="recovery",
            )
        return recovered
