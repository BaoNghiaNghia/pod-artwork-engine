from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import psutil
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .contracts import JobRecord, QualityMode
from .engine import Engine
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    engine = Engine(settings)

    app = FastAPI(title="POD Artwork Engine", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "tauri://localhost",
            "http://localhost:1420",
            "http://127.0.0.1:1420",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/status")
    def status() -> dict:
        memory = psutil.virtual_memory()
        return {
            "version": __version__,
            "storage": engine.storage.status().model_dump(mode="json"),
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": memory.percent,
            "memory_available_bytes": memory.available,
        }

    @app.get("/jobs", response_model=list[JobRecord])
    def list_jobs(limit: int = 50) -> list[JobRecord]:
        return engine.jobs.list_recent(limit)

    @app.get("/jobs/{job_id}", response_model=JobRecord)
    def get_job(job_id: str) -> JobRecord:
        job = engine.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.post("/jobs", response_model=JobRecord)
    async def create_job(
        files: list[UploadFile] = File(...),
        quality_mode: QualityMode = QualityMode.PRINT_READY,
    ) -> JobRecord:
        if not files:
            raise HTTPException(status_code=400, detail="At least one input image is required")

        staging = Path(tempfile.mkdtemp(prefix="pod-upload-", dir=settings.data_root))
        staged_paths: list[Path] = []
        try:
            for upload in files:
                safe_name = Path(upload.filename or "input.bin").name
                target = staging / safe_name
                with target.open("wb") as handle:
                    while chunk := await upload.read(1024 * 1024):
                        handle.write(chunk)
                staged_paths.append(target)

            job = engine.create_job(staged_paths, quality_mode)
            asyncio.create_task(asyncio.to_thread(engine.run_preflight, job.job_id))
            return job
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    return app
