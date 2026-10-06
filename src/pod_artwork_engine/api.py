from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
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
from .harness import HarnessStore
from .harness_models import BenchmarkScorecard
from .local_ocr import backend_status as local_ocr_backend_status
from .settings import Settings
from .updater import UpdateManager


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    engine = Engine(settings)
    datasets = DatasetRegistry(settings.database_path, settings.datasets_dir)
    harness = HarnessStore(settings.harness_dir)
    hardware = detect_hardware()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        recovered = engine.recover_interrupted_jobs()
        for job in recovered:
            if job.state is JobState.RESUMING:
                asyncio.create_task(asyncio.to_thread(engine.run_job, job.job_id))
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
        ocr_status = local_ocr_backend_status(settings)
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
            "harness_run_count": len(harness.list_scorecards()),
            "remote_provider_configured": engine.provider.available,
            "remote_provider_name": settings.remote_provider_name,
            "qc_policy_id": engine.qc_policy.policy_id,
            "qc_policy_version": engine.qc_policy.version,
            "qc_policy_configured": settings.qc_policy_path is not None,
            "router_policy_id": engine.router_policy.policy_id,
            "router_policy_version": engine.router_policy.version,
            "router_policy_configured": settings.router_policy_path is not None,
            "local_ocr_enabled": settings.local_ocr_enabled,
            "local_ocr_available": bool(ocr_status["available"]),
            "local_ocr_backend": ocr_status,
            "visual_font_match_enabled": settings.visual_font_match_enabled,
            "visual_font_match_min_score": settings.visual_font_match_min_score,
            "visual_font_match_min_margin": settings.visual_font_match_min_margin,
            "reference_fusion_method": "deterministic_reference_fusion_v1",
            "region_confidence_method": "normalized_region_evidence_v1",
            "region_rescue_planner_method": "region_rescue_plan_v1",
            "representation_planner_method": "representation_plan_v1",
            "material_separation_method": "material_separation_evidence_v1",
            "texture_handling_method": "texture_handling_evidence_v1",
            "super_resolution_method": "super_resolution_readiness_v1",
            "sr_benchmark_method": "sr_benchmark_matrix_v1",
            "sr_adapter_method": "sr_adapter_materializer_v1",
            "sr_cohort_method": "sr_fair_cohort_v1",
            "geometry_path_capabilities": [
                "cubic",
                "multi_subpath",
                "evenodd_compound_fill",
            ],
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

    @app.get("/harness/runs", response_model=list[BenchmarkScorecard])
    def list_harness_runs() -> list[BenchmarkScorecard]:
        return harness.list_scorecards()

    @app.get("/harness/runs/{run_id}", response_model=BenchmarkScorecard)
    def get_harness_run(run_id: str) -> BenchmarkScorecard:
        scorecard = harness.get_scorecard(run_id)
        if not scorecard:
            raise HTTPException(status_code=404, detail="Harness run not found")
        return scorecard

    @app.get("/jobs", response_model=list[JobRecord])
    def list_jobs(limit: int = 50) -> list[JobRecord]:
        return engine.jobs.list_recent(limit)

    @app.get("/jobs/{job_id}", response_model=JobRecord)
    def get_job(job_id: str) -> JobRecord:
        job = engine.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.get("/jobs/{job_id}/output")
    def get_job_output(job_id: str):
        job = engine.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if not job.result_path:
            raise HTTPException(status_code=404, detail="Output not available")
        path = Path(job.result_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Output file missing")
        return FileResponse(
            path,
            media_type="image/png",
        )

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
            asyncio.create_task(asyncio.to_thread(engine.run_job, job.job_id))
            return job
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @app.post("/storage/cleanup")
    def cleanup_storage() -> dict[str, int]:
        return engine.storage.cleanup()

    return app
