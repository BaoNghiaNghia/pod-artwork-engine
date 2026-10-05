from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .contracts import FailureCategory, JobRecord, JobState


TERMINAL_STATES = {
    JobState.COMPLETED,
    JobState.FAILED_FINAL,
    JobState.CANCELLED,
    JobState.REVIEW_REQUIRED,
}


class JobStore:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def save(self, job: JobRecord) -> JobRecord:
        job.updated_at = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs(job_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                  payload=excluded.payload,
                  updated_at=excluded.updated_at
                """,
                (job.job_id, job.model_dump_json(), job.updated_at.isoformat()),
            )
        return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return JobRecord.model_validate_json(row["payload"]) if row else None

    def list_recent(self, limit: int = 50) -> list[JobRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM jobs ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [JobRecord.model_validate_json(row["payload"]) for row in rows]

    def list_interrupted(self, limit: int = 200) -> list[JobRecord]:
        jobs = self.list_recent(limit)
        return [job for job in jobs if job.state not in TERMINAL_STATES]

    def transition(
        self,
        job_id: str,
        state: JobState,
        *,
        progress: float | None = None,
        message: str | None = None,
        failure_category: FailureCategory | None = None,
        failure_reason: str | None = None,
        result_path: str | None = None,
    ) -> JobRecord:
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        job.state = state
        if progress is not None:
            job.progress = progress
        if message is not None:
            job.stage_message = message
        job.failure_category = failure_category
        job.failure_reason = failure_reason
        if result_path is not None:
            job.result_path = result_path
        return self.save(job)
