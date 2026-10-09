from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import BaseModel, Field

import psutil
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .local_draft import local_draft_for_job, preview_kind_for_job, marked_preview_for_job
from .image_chat import ImageChatService, ImageChatConfig, ImageChatRequest, ImageChatError
from .provider_control import ProviderConfigUpdate, apply_provider_config, sanitized_config, test_provider_contract
from .providers import ProviderUnavailable, ProviderProtocolError
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
from .golden_preflight import GoldenHoldoutPreflight, GoldenHoldoutPreflightBuilder
from .hardware import detect_hardware
from .historical_import import HistoricalImporter
from .pair_previews import PairPreviewSessions
from .onboarding_workflow import (
    OnboardingImportRequest,
    OnboardingPreviewResponse,
    OnboardingRequest,
    preview as onboarding_preview,
)
from .harness import HarnessStore
from .harness_models import BenchmarkScorecard
from .local_ocr import backend_status as local_ocr_backend_status
from .scheduler import recommended_job_concurrency
from .settings import Settings
from .updater import UpdateManager


class GoldenPreflightRequest(BaseModel):
    dataset_id: str | None = None
    recipe_path: str | None = None
    local_sr_adapter_path: str | None = None
    remote_sr_adapter_path: str | None = None
    minimum_golden_cases: int = Field(default=3, ge=1, le=10000)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    engine = Engine(settings)
    datasets = DatasetRegistry(settings.database_path, settings.datasets_dir)
    harness = HarnessStore(settings.harness_dir)
    hardware = detect_hardware()
    job_concurrency = recommended_job_concurrency(settings)
    job_slots = asyncio.Semaphore(job_concurrency)
    onboarding_import_lock = threading.Lock()
    pair_preview_sessions = PairPreviewSessions()
    image_chat = ImageChatService(settings)

    async def run_job_with_slot(job_id: str) -> None:
        async with job_slots:
            await asyncio.to_thread(engine.run_job, job_id)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        recovered = engine.recover_interrupted_jobs()
        for job in recovered:
            if job.state is JobState.RESUMING:
                asyncio.create_task(run_job_with_slot(job.job_id))
        yield

    app = FastAPI(title="POD Artwork Engine", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            # Tauri 2 WebView2 loads packaged assets from this origin on Windows.
            # Keep an explicit allowlist; the engine is bound to loopback only.
            "http://tauri.localhost",
            "https://tauri.localhost",
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
            "remote_provider_configured": engine.provider.configured,
            "remote_provider_available": engine.provider.available,
            "remote_provider_state": engine.provider.status(),
            "remote_provider_name": settings.remote_provider_name,
            "job_concurrency": job_concurrency,
            "performance": engine.performance.summary(limit=500),
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
            "reference_alignment_method": "geometric_reference_alignment_v1",
            "feature_correspondence_method": "feature_correspondence_benchmark_v1",
            "registration_calibration_method": "golden_registration_calibration_v1",
            "dewarp_benchmark_method": "benchmark_dewarp_registration_v1",
            "dewarp_experiment_method": "golden_dewarp_experiment_v1",
            "production_registration_policy_method": "production_registration_policy_proposal_v1",
            "region_confidence_method": "normalized_region_evidence_v1",
            "region_rescue_planner_method": "region_rescue_plan_v1",
            "representation_planner_method": "representation_plan_v1",
            "material_separation_method": "material_separation_evidence_v1",
            "material_separation_benchmark_method": "material_separation_benchmark_v1",
            "material_separation_experiment_method": "golden_material_separation_experiment_v1",
            "material_separation_policy_method": "material_separation_policy_proposal_v1",
            "policy_review_packet_method": "human_policy_review_packet_v2",
            "policy_decision_receipt_method": "human_policy_decision_receipt_v1",
            "policy_activation_readiness_method": "human_policy_activation_readiness_v1",
            "golden_holdout_preflight_method": "golden_holdout_preflight_v1",
            "historical_onboarding_method": "historical_dataset_onboarding_v1",
            "texture_handling_method": "texture_handling_evidence_v1",
            "super_resolution_method": "super_resolution_readiness_v1",
            "sr_benchmark_method": "sr_benchmark_matrix_v1",
            "sr_adapter_method": "sr_adapter_materializer_v1",
            "sr_cohort_method": "sr_fair_cohort_v1",
            "sr_experiment_method": "sr_golden_experiment_v1",
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

    @app.post("/historical/onboarding/preflight", response_model=OnboardingPreviewResponse)
    def historical_onboarding_preflight(request: OnboardingRequest) -> OnboardingPreviewResponse:
        try:
            result = onboarding_preview(request)
            result.preview_token = pair_preview_sessions.register(result.report)
            return result
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/historical/onboarding/previews/{token}/{pair_index}/{role}")
    def historical_pair_thumbnail(
        token: str,
        pair_index: int,
        role: str,
        source_index: int = 0,
        size: str = "thumb",
    ) -> Response:
        if size not in {"thumb", "large"}:
            raise HTTPException(status_code=404, detail="Unknown preview size")
        if role not in {"source", "target"}:
            raise HTTPException(status_code=404, detail="Unknown preview role")
        try:
            image = pair_preview_sessions.image(
                token,
                pair_index,
                role,
                source_index=source_index,
                size=size,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(
            content=image,
            media_type="image/webp",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/historical/onboarding/import")
    def historical_onboarding_import(request: OnboardingImportRequest) -> dict:
        # Explicit mutation boundary. Always revalidate files and readiness.
        with onboarding_import_lock:
            try:
                current = onboarding_preview(OnboardingRequest.model_validate(
                    request.model_dump(exclude={"snapshot_id", "dataset_name", "confirmation"})
                ))
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            if not current.report.ready_to_import:
                raise HTTPException(
                    status_code=409,
                    detail={"reason": "preflight_blocked", "blockers": current.report.blockers},
                )
            if current.snapshot_id != request.snapshot_id:
                raise HTTPException(
                    status_code=409,
                    detail="Dataset files or onboarding options changed; run preflight again",
                )

            importer = HistoricalImporter(datasets)
            try:
                if request.mode == "manifest":
                    result = importer.import_manifest(
                        Path(request.manifest_path or ""),
                        dataset_name=request.dataset_name,
                        seed=request.seed,
                    )
                else:
                    result = importer.import_folders(
                        Path(request.source_dir or ""),
                        Path(request.target_dir or ""),
                        dataset_name=request.dataset_name,
                        id_regex=request.id_regex or None,
                        seed=request.seed,
                        allow_visual_fallback=request.allow_visual_fallback,
                    )
            except (ValueError, OSError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            return {
                **result.to_dict(),
                "production_execution_enabled": False,
            }

    @app.post("/harness/golden-preflight", response_model=GoldenHoldoutPreflight)
    def golden_preflight(request: GoldenPreflightRequest) -> GoldenHoldoutPreflight:
        # Read-only evidence preflight: no benchmark execution or policy activation.
        return GoldenHoldoutPreflightBuilder(settings, datasets).build(
            dataset_id=request.dataset_id,
            recipe_path=Path(request.recipe_path) if request.recipe_path else None,
            local_sr_adapter_path=(
                Path(request.local_sr_adapter_path) if request.local_sr_adapter_path else None
            ),
            remote_sr_adapter_path=(
                Path(request.remote_sr_adapter_path) if request.remote_sr_adapter_path else None
            ),
            minimum_golden_cases=request.minimum_golden_cases,
        )

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
        # Legacy jobs may point to an enlarged garment mockup despite a
        # failed semantic gate. Never serve that artifact as a 2D print.
        if job.failure_reason and (
            "semantic_provider_not_used" in job.failure_reason
            or "artwork_not_isolated_from_product_mockup" in job.failure_reason
        ):
            raise HTTPException(
                status_code=409,
                detail="Artwork was not isolated from the product mockup; "
                "a semantic reconstruction provider is required",
            )
        if job.state is not JobState.COMPLETED:
            raise HTTPException(status_code=409, detail="Print-ready export requires a completed QC-approved job")
        if not job.result_path:
            raise HTTPException(status_code=404, detail="2D output not available")
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
            asyncio.create_task(run_job_with_slot(job.job_id))
            return job
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @app.get("/provider/config")
    def provider_config() -> dict:
        return sanitized_config(engine.provider)

    @app.post("/provider/config")
    def set_provider_config(request: ProviderConfigUpdate) -> dict:
        try:
            new_provider = apply_provider_config(engine.provider, request)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        engine.provider = new_provider
        engine.settings = new_provider.settings
        return sanitized_config(new_provider)

    @app.post("/provider/test")
    async def test_provider() -> dict:
        try:
            return await asyncio.to_thread(test_provider_contract, engine.provider)
        except (ProviderUnavailable, ProviderProtocolError, ValueError, OSError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/jobs/{job_id}/retry-ai", response_model=JobRecord)
    async def retry_with_ai(job_id: str) -> JobRecord:
        previous = engine.jobs.get(job_id)
        if previous is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if previous.state not in {JobState.REVIEW_REQUIRED, JobState.FAILED_FINAL}:
            raise HTTPException(status_code=409, detail="Retry requires a stopped or review-needed job")
        if not engine.provider.available:
            raise HTTPException(status_code=409, detail="Configure an available AI provider before retrying")
        if not previous.source_paths or not all(Path(path).is_file() for path in previous.source_paths):
            raise HTTPException(status_code=404, detail="Original source images are missing")
        retried = engine.create_job(
            [Path(path) for path in previous.source_paths],
            previous.quality_mode,
            force_remote=True,
            parent_job_id=previous.job_id,
        )
        asyncio.create_task(run_job_with_slot(retried.job_id))
        return retried

    @app.get("/jobs/{job_id}/preview-info")
    def get_preview_info(job_id: str) -> dict:
        job = engine.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "kind": preview_kind_for_job(settings.jobs_dir, job_id)
            if job.state is JobState.REVIEW_REQUIRED else None,
            "print_ready": job.state is JobState.COMPLETED,
        }

    @app.get("/jobs/{job_id}/ai-candidate")
    def get_ai_candidate(job_id: str):
        job = engine.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        candidate = marked_preview_for_job(
            settings.jobs_dir, job_id, kind="ai_candidate",
            review_required=job.state is JobState.REVIEW_REQUIRED,
        )
        if candidate is None:
            raise HTTPException(status_code=404, detail="AI candidate preview not available")
        return FileResponse(candidate, media_type="image/png", headers={
            "Cache-Control": "private, no-store",
            "X-POD-Output-Type": "ai-candidate-review-only",
        })

    @app.get("/jobs/{job_id}/draft")
    def get_local_draft(job_id: str):
        job = engine.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        blocked = bool(job.state is JobState.REVIEW_REQUIRED and job.failure_reason
            and ("artwork_not_isolated_from_product_mockup" in job.failure_reason
                 or "semantic_provider_not_used" in job.failure_reason))
        draft = local_draft_for_job(settings.jobs_dir, job_id, blocked=blocked)
        if draft is None:
            raise HTTPException(status_code=404, detail="Local preview-only draft unavailable")
        return FileResponse(draft, media_type="image/png", headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-POD-Output-Type": "draft-not-print-ready",
        })

    @app.post("/image-chat/sessions", response_model=JobRecord)
    async def create_image_chat_session(files: list[UploadFile] = File(...)) -> JobRecord:
        if not 1 <= len(files) <= 10:
            raise HTTPException(status_code=400, detail="Choose 1 to 10 reference images")
        staging = Path(tempfile.mkdtemp(prefix="pod-chat-", dir=settings.data_root))
        staged_paths: list[Path] = []
        try:
            for index, upload in enumerate(files):
                safe_name = Path(upload.filename or f"reference-{index}.png").name
                target = staging / f"{index:02d}-{safe_name}"
                total = 0
                with target.open("wb") as handle:
                    while chunk := await upload.read(1024 * 1024):
                        total += len(chunk)
                        if total > 15 * 1024 * 1024:
                            raise HTTPException(status_code=413, detail="Each image must be under 15 MB")
                        handle.write(chunk)
                from PIL import Image
                try:
                    with Image.open(target) as image:
                        image.verify()
                except (OSError, ValueError) as exc:
                    raise HTTPException(status_code=400, detail="Invalid reference image") from exc
                staged_paths.append(target)
            return engine.create_job(staged_paths, QualityMode.QUICK_2D)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @app.post("/jobs/{job_id}/image-chat/{version_id}/print-check", response_model=JobRecord)
    async def image_chat_print_check(job_id: str, version_id: str) -> JobRecord:
        previous = engine.jobs.get(job_id)
        if previous is None:
            raise HTTPException(status_code=404, detail="Job not found")
        image = image_chat.image_path(job_id, version_id)
        if image is None:
            raise HTTPException(status_code=409, detail="AI version is not ready")
        checked = engine.create_job(
            [image], QualityMode.PRINT_READY, parent_job_id=job_id,
        )
        asyncio.create_task(run_job_with_slot(checked.job_id))
        return checked

    @app.get("/image-chat/config")
    def image_chat_config() -> dict:
        return image_chat.status()

    @app.post("/image-chat/config")
    def set_image_chat_config(request: ImageChatConfig) -> dict:
        try:
            return image_chat.configure(request)
        except ImageChatError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/jobs/{job_id}/image-chat/source")
    def image_chat_source(job_id: str):
        job = engine.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        source = image_chat.source_for_job(job)
        if source is None:
            raise HTTPException(status_code=404, detail="Source image not found")
        from PIL import Image, ImageOps
        from io import BytesIO
        with Image.open(source) as original:
            picture = ImageOps.exif_transpose(original).convert("RGBA")
            picture.thumbnail((900, 900), Image.Resampling.LANCZOS)
            content = BytesIO()
            picture.save(content, format="PNG")
        return Response(
            content=content.getvalue(), media_type="image/png",
            headers={"Cache-Control": "private, no-store",
                     "X-POD-Output-Type": "source-preview"},
        )

    @app.get("/jobs/{job_id}/image-chat")
    def image_chat_versions(job_id: str) -> list[dict]:
        if not engine.jobs.get(job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        return image_chat.list_versions(job_id)

    @app.post("/jobs/{job_id}/image-chat", status_code=202)
    async def edit_image_with_chat(job_id: str, request: ImageChatRequest) -> dict:
        job = engine.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            version = image_chat.start(job, request)
        except ImageChatError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        asyncio.create_task(asyncio.to_thread(image_chat.generate, job, version["version_id"]))
        return version

    @app.get("/jobs/{job_id}/image-chat/{version_id}/image")
    def image_chat_result(job_id: str, version_id: str):
        if not engine.jobs.get(job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        path = image_chat.image_path(job_id, version_id)
        if path is None:
            raise HTTPException(status_code=404, detail="Image version not ready")
        return FileResponse(path, media_type="image/png", headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-POD-Output-Type": "ai-edit-preview-not-print-ready",
        })

    @app.get("/performance")
    def performance(limit: int = 1000) -> dict:
        return {
            "job_concurrency": job_concurrency,
            "provider": engine.provider.status(),
            "stages": engine.performance.summary(limit=limit),
        }

    @app.post("/storage/cleanup")
    def cleanup_storage() -> dict[str, int]:
        return engine.storage.cleanup()

    return app
