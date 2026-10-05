from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .contracts import (
    DatasetMember,
    DatasetRecord,
    DatasetSplit,
    HistoricalPair,
    JobRecord,
    JobState,
    QualityMode,
)
from .dataset_registry import DatasetRegistry
from .engine import Engine
from .hardware import detect_hardware
from .settings import Settings
from .updater import UpdateManager


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    engine = Engine(settings)
    datasets = DatasetRegistry(settings.database_path, settings.datasets_dir)
    hardware = detect_hardware()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        recovered = engine.recover_interrupted_jobs()
        for job in recovered:
            if job.state is JobState.RESUMING:
                asyncio.create_task(asyncio.to_thread(engine.run_preflight, job.job_id))
        yield

    app = FastAPI(title="POD Artwork Engine", version=__version__, lifespan=lifespan)
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
        return {
            "status": "ok",
            "version": __version__,
            "instance_token": os.environ.get("POD_BOOTSTRAP_TOKEN"),
        }

    @app.get("/status")
    def status() -> dict:
        memory = psutil.virtual_memory()
        historical_pairs = datasets.list_pairs()
        dataset_records = datasets.list_datasets()
        return {
            "version": __version__,
            "storage": engine.storage.status().model_dump(mode="json"),
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": memory.percent,
            "memory_available_bytes": memory.available,
            "hardware": hardware.to_dict(),
            "update": UpdateManager(settings, __version__).state(),
            "historical_pair_count": len(historical_pairs),
            "dataset_count": len(dataset_records),
        }

    @app.get("/datasets", response_model=list[DatasetRecord])
    def list_datasets() -> list[DatasetRecord]:
        return datasets.list_datasets()

    @app.get("/datasets/{dataset_id}", response_model=DatasetRecord)
    def get_dataset(dataset_id: str) -> DatasetRecord:
        dataset = datasets.get_dataset(dataset_id)
        if not dataset:
            raise HTTPException(status_code=404, detail="Dataset not found")
        return dataset

    @app.get("/datasets/{dataset_id}/members", response_model=list[DatasetMember])
    def list_dataset_members(
        dataset_id: str,
        split: DatasetSplit | None = None,
        retrieval_only: bool = False,
    ) -> list[DatasetMember]:
        if not datasets.get_dataset(dataset_id):
            raise HTTPException(status_code=404, detail="Dataset not found")
        return datasets.list_members(
            dataset_id,
            split=split,
            retrieval_only=retrieval_only,
        )

    @app.get("/historical/pairs", response_model=list[HistoricalPair])
    def list_historical_pairs() -> list[HistoricalPair]:
        return datasets.list_pairs()

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

    @app.post("/storage/cleanup")
    def cleanup_storage() -> dict[str, int]:
        return engine.storage.cleanup()

    return app
