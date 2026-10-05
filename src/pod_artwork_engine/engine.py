from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from .analyzer import analyze_locally
from .checkpoints import CheckpointManager
from .contracts import (
    ArtifactManifest,
    ArtifactRef,
    DesignSpec,
    ExportProfile,
    FailureCategory,
    JobRecord,
    JobState,
    PreflightResult,
    ProviderRequest,
    ProviderResult,
    QCResult,
    QualityMode,
    RouteDecision,
)
from .exporter import export_master
from .job_store import JobStore
from .logging_config import log_event
from .preflight import inspect_image, sha256_file
from .providers import (
    ProviderProtocolError,
    ProviderUnavailable,
    RemoteProvider,
    materialize_provider_candidate,
)
from .qc import semantic_qc, technical_qc
from .reconstruction import CandidateInfo, normalize_candidate, reconstruct_local_baseline
from .resources import capture_resources
from .router import choose_route
from .settings import Settings
from .storage import StorageLimitExceeded, StorageManager


logger = logging.getLogger("pod_engine")


class Engine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_directories()
        self.storage = StorageManager(settings)
        self.jobs = JobStore(settings.database_path)
        self.checkpoints = CheckpointManager(settings.jobs_dir)
        self.provider = RemoteProvider(settings)

    def _job_dir(self, job_id: str) -> Path:
        return self.settings.jobs_dir / job_id

    def _log_stage(
        self,
        job: JobRecord,
        event: str,
        *,
        stage: str,
        duration_ms: int | None = None,
        provider: str | None = None,
        model_version: str | None = None,
        route: str | None = None,
        quality_score: float | None = None,
        failure_reason: str | None = None,
    ) -> None:
        resources = capture_resources(self.storage)
        fields = {
            "job_id": job.job_id,
            "trace_id": job.trace_id,
            "stage": stage,
            "cpu_percent": round(resources.cpu_percent, 2),
            "process_rss_bytes": resources.process_rss_bytes,
            "memory_available_bytes": resources.memory_available_bytes,
            "storage_used_bytes": resources.storage_used_bytes,
        }
        if duration_ms is not None:
            fields["duration_ms"] = duration_ms
        if provider:
            fields["provider"] = provider
        if model_version:
            fields["model_version"] = model_version
        if route:
            fields["route"] = route
        if quality_score is not None:
            fields["quality_score"] = round(quality_score, 4)
        if failure_reason:
            fields["failure_reason"] = failure_reason
        log_event(logger, event, **fields)

    def create_job(self, source_paths: list[Path], quality_mode: QualityMode) -> JobRecord:
        required = sum(path.stat().st_size for path in source_paths if path.exists())
        self.storage.assert_capacity(required)
        job = JobRecord(
            quality_mode=quality_mode,
            source_paths=[str(path) for path in source_paths],
        )
        job_dir = self._job_dir(job.job_id)
        (job_dir / "source").mkdir(parents=True, exist_ok=True)
        (job_dir / "checkpoints").mkdir(parents=True, exist_ok=True)
        (job_dir / "temp").mkdir(parents=True, exist_ok=True)
        (job_dir / "master").mkdir(parents=True, exist_ok=True)
        (job_dir / "final").mkdir(parents=True, exist_ok=True)

        copied_sources: list[str] = []
        for index, source in enumerate(source_paths, start=1):
            safe_name = source.name
            target = job_dir / "source" / safe_name
            if target.exists():
                target = job_dir / "source" / f"{index:02d}-{safe_name}"
            shutil.copy2(source, target)
            copied_sources.append(str(target))
        job.source_paths = copied_sources
        self.jobs.save(job)
        self._log_stage(job, "job_created", stage="queued")
        return job

    def _load_preflights(self, job: JobRecord) -> list[PreflightResult] | None:
        payload = self.checkpoints.payload(job.job_id, "preflight")
        if not isinstance(payload, list):
            return None
        return [PreflightResult.model_validate(item) for item in payload]

    def _perform_preflight(self, job: JobRecord) -> list[PreflightResult]:
        existing = self._load_preflights(job)
        if existing is not None:
            self._log_stage(job, "preflight_checkpoint_reused", stage="preflight")
            return existing

        self.jobs.transition(
            job.job_id,
            JobState.PREFLIGHT,
            progress=0.05,
            message="Inspecting source quality and artwork region",
        )
        started = time.perf_counter()
        results = [inspect_image(Path(path)) for path in job.source_paths]
        self.checkpoints.write(
            job.job_id,
            "preflight",
            [result.model_dump(mode="json") for result in results],
        )
        refreshed = self.jobs.get(job.job_id) or job
        self._log_stage(
            refreshed,
            "preflight_completed",
            stage="preflight",
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return results

    def run_preflight(self, job_id: str) -> JobRecord:
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        try:
            self._perform_preflight(job)
            return self.jobs.transition(
                job_id,
                JobState.WAITING_PROVIDER,
                progress=0.12,
                message="Pre-flight complete",
            )
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

    def _analyze(
        self,
        job: JobRecord,
        source_paths: list[Path],
        preflights: list[PreflightResult],
    ) -> tuple[DesignSpec, ProviderResult | None]:
        checkpoint = self.checkpoints.payload(job.job_id, "design_spec")
        if isinstance(checkpoint, dict):
            return DesignSpec.model_validate(checkpoint), None

        self.jobs.transition(
            job.job_id,
            JobState.ANALYZING,
            progress=0.18,
            message="Analyzing artwork structure",
        )
        started = time.perf_counter()
        local_spec = analyze_locally(source_paths, preflights)
        provider_result: ProviderResult | None = None
        needs_remote_analysis = self.provider.available and (
            "need_semantic_reconstruction" in local_spec.required_capabilities
            or local_spec.confidence < 0.55
            or job.quality_mode is QualityMode.MAX_FIDELITY
        )

        if needs_remote_analysis:
            try:
                provider_result = self.provider.execute(
                    ProviderRequest(
                        action="analyze",
                        job_id=job.job_id,
                        quality_mode=job.quality_mode,
                        source_paths=[str(path) for path in source_paths],
                        design_spec=local_spec,
                        requested_capabilities=["need_design_spec"],
                    )
                )
                if provider_result.design_spec is not None:
                    local_spec = provider_result.design_spec
            except (ProviderUnavailable, ProviderProtocolError) as exc:
                self._log_stage(
                    job,
                    "semantic_analyzer_fallback",
                    stage="analyzing",
                    failure_reason=str(exc),
                )

        self.checkpoints.write(
            job.job_id,
            "design_spec",
            local_spec.model_dump(mode="json"),
        )
        self._log_stage(
            job,
            "analysis_completed",
            stage="analyzing",
            duration_ms=round((time.perf_counter() - started) * 1000),
            provider=provider_result.provider if provider_result else None,
            model_version=provider_result.model_version if provider_result else None,
        )
        return local_spec, provider_result

    def _route(self, job: JobRecord, design_spec: DesignSpec) -> RouteDecision:
        checkpoint = self.checkpoints.payload(job.job_id, "route")
        if isinstance(checkpoint, dict):
            return RouteDecision.model_validate(checkpoint)

        decision = choose_route(
            design_spec,
            job.quality_mode,
            remote_available=self.provider.available,
        )
        self.checkpoints.write(job.job_id, "route", decision.model_dump(mode="json"))
        self._log_stage(
            job,
            "route_selected",
            stage="routing",
            route=decision.route.value,
        )
        return decision

    def _reconstruct(
        self,
        job: JobRecord,
        source_paths: list[Path],
        preflights: list[PreflightResult],
        design_spec: DesignSpec,
        route: RouteDecision,
    ) -> tuple[CandidateInfo, ProviderResult | None, bool]:
        candidate_checkpoint = self.checkpoints.payload(job.job_id, "candidate")
        if isinstance(candidate_checkpoint, dict):
            path = Path(str(candidate_checkpoint.get("path", "")))
            if path.is_file():
                info = CandidateInfo(
                    path=path,
                    source_path=Path(str(candidate_checkpoint.get("source_path", path))),
                    native_width=int(candidate_checkpoint["native_width"]),
                    native_height=int(candidate_checkpoint["native_height"]),
                    alpha_method=str(candidate_checkpoint["alpha_method"]),
                    local_baseline=bool(candidate_checkpoint["local_baseline"]),
                )
                provider_name = candidate_checkpoint.get("provider")
                restored_provider = None
                if provider_name:
                    restored_provider = ProviderResult(
                        provider=str(provider_name),
                        model_version=str(candidate_checkpoint.get("model_version") or ""),
                        recognized_text=list(candidate_checkpoint.get("recognized_text") or []),
                    )
                return info, restored_provider, bool(candidate_checkpoint.get("used_remote"))

        self.jobs.transition(
            job.job_id,
            JobState.RECONSTRUCTING,
            progress=0.42,
            message="Reconstructing clean 2D artwork",
        )
        started = time.perf_counter()
        provider_result: ProviderResult | None = None
        used_remote = False
        temp_dir = self._job_dir(job.job_id) / "temp"

        if route.use_remote_provider and self.provider.available:
            try:
                provider_result = self.provider.execute(
                    ProviderRequest(
                        action="reconstruct",
                        job_id=job.job_id,
                        quality_mode=job.quality_mode,
                        source_paths=[str(path) for path in source_paths],
                        design_spec=design_spec,
                        requested_capabilities=route.required_capabilities,
                    )
                )
                provider_candidate = materialize_provider_candidate(
                    provider_result,
                    temp_dir / "provider-candidate.png",
                )
                if provider_candidate is not None:
                    candidate = normalize_candidate(
                        provider_candidate,
                        temp_dir / "candidate.png",
                        source_path=source_paths[0],
                    )
                    used_remote = True
                else:
                    raise ProviderProtocolError("provider returned no reconstruction image")
            except (ProviderUnavailable, ProviderProtocolError) as exc:
                self._log_stage(
                    job,
                    "reconstruction_provider_fallback",
                    stage="reconstructing",
                    failure_reason=str(exc),
                    route=route.route.value,
                )
                candidate = reconstruct_local_baseline(
                    source_paths,
                    preflights,
                    design_spec,
                    temp_dir / "candidate.png",
                )
        else:
            candidate = reconstruct_local_baseline(
                source_paths,
                preflights,
                design_spec,
                temp_dir / "candidate.png",
            )

        self.checkpoints.write(
            job.job_id,
            "candidate",
            {
                "path": str(candidate.path),
                "source_path": str(candidate.source_path),
                "native_width": candidate.native_width,
                "native_height": candidate.native_height,
                "alpha_method": candidate.alpha_method,
                "local_baseline": candidate.local_baseline,
                "used_remote": used_remote,
                "provider": provider_result.provider if provider_result else None,
                "model_version": provider_result.model_version if provider_result else None,
                "recognized_text": provider_result.recognized_text if provider_result else [],
            },
        )
        self._log_stage(
            job,
            "reconstruction_completed",
            stage="reconstructing",
            duration_ms=round((time.perf_counter() - started) * 1000),
            provider=provider_result.provider if provider_result else None,
            model_version=provider_result.model_version if provider_result else None,
            route=route.route.value,
        )
        return candidate, provider_result, used_remote

    def _write_artifact_manifest(
        self,
        job: JobRecord,
        preflights: list[PreflightResult],
        semantic_master: Path,
        final_path: Path,
        qc1: QCResult,
        qc2: QCResult,
        provider_result: ProviderResult | None,
    ) -> Path:
        manifest = ArtifactManifest(
            job_id=job.job_id,
            source_hashes=[item.sha256 for item in preflights],
            artifacts=[
                ArtifactRef(
                    kind="semantic_master",
                    path=str(semantic_master),
                    sha256=sha256_file(semantic_master),
                    size_bytes=semantic_master.stat().st_size,
                ),
                ArtifactRef(
                    kind="final_master",
                    path=str(final_path),
                    sha256=sha256_file(final_path),
                    size_bytes=final_path.stat().st_size,
                ),
            ],
            model_versions=(
                {provider_result.provider: provider_result.model_version}
                if provider_result and provider_result.model_version
                else {}
            ),
            policy_version="phase1-v1",
            export_profile="default_pod",
            qc_report={
                "semantic": qc1.model_dump(mode="json"),
                "technical": qc2.model_dump(mode="json"),
            },
        )
        path = self._job_dir(job.job_id) / "master" / "artifact_manifest.json"
        path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return path

    def run_job(self, job_id: str) -> JobRecord:
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        if job.state in {
            JobState.COMPLETED,
            JobState.REVIEW_REQUIRED,
            JobState.FAILED_FINAL,
            JobState.CANCELLED,
        }:
            return job

        try:
            source_paths = [Path(path) for path in job.source_paths]
            preflights = self._perform_preflight(job)
            job = self.jobs.get(job_id) or job

            profile = ExportProfile()
            estimated_working_bytes = profile.width * profile.height * 4 * 2
            self.storage.assert_capacity(estimated_working_bytes)

            design_spec, analysis_provider = self._analyze(
                job,
                source_paths,
                preflights,
            )
            job = self.jobs.get(job_id) or job
            route = self._route(job, design_spec)

            candidate, reconstruction_provider, used_remote = self._reconstruct(
                job,
                source_paths,
                preflights,
                design_spec,
                route,
            )
            provider_result = reconstruction_provider or analysis_provider

            self.jobs.transition(
                job_id,
                JobState.QC,
                progress=0.60,
                message="Checking source fidelity",
            )
            recognized_text = (
                reconstruction_provider.recognized_text
                if reconstruction_provider is not None
                else []
            )
            qc1 = semantic_qc(
                design_spec,
                job.quality_mode,
                recognized_text=recognized_text,
                used_remote_provider=used_remote,
            )
            self.checkpoints.write(job_id, "qc_semantic", qc1.model_dump(mode="json"))
            self._log_stage(
                job,
                "semantic_qc_completed",
                stage="qc_semantic",
                quality_score=qc1.score,
                provider=provider_result.provider if provider_result else None,
                model_version=provider_result.model_version if provider_result else None,
            )

            self.jobs.transition(
                job_id,
                JobState.PRECISION_FINISHING,
                progress=0.72,
                message="Refining alpha and rendering POD master",
            )
            semantic_master = self._job_dir(job_id) / "master" / "semantic-master.png"
            normalized = normalize_candidate(
                candidate.path,
                semantic_master,
                source_path=candidate.source_path,
            )
            # Preserve native evidence for technical QC instead of treating the
            # normalized copy as newly recovered resolution.
            normalized = CandidateInfo(
                path=normalized.path,
                source_path=normalized.source_path,
                native_width=candidate.native_width,
                native_height=candidate.native_height,
                alpha_method=candidate.alpha_method,
                local_baseline=candidate.local_baseline,
            )
            final_path = export_master(
                semantic_master,
                self._job_dir(job_id) / "final" / "4500x5400.png",
                profile,
            )

            self.jobs.transition(
                job_id,
                JobState.QC,
                progress=0.88,
                message="Checking technical print quality",
            )
            qc2 = technical_qc(
                final_path,
                normalized,
                job.quality_mode,
                profile,
            )
            self.checkpoints.write(job_id, "qc_technical", qc2.model_dump(mode="json"))
            self._log_stage(
                job,
                "technical_qc_completed",
                stage="qc_technical",
                quality_score=qc2.score,
            )
            manifest_path = self._write_artifact_manifest(
                job,
                preflights,
                semantic_master,
                final_path,
                qc1,
                qc2,
                provider_result,
            )
            self.checkpoints.write(
                job_id,
                "final",
                {
                    "result_path": str(final_path),
                    "artifact_manifest": str(manifest_path),
                    "semantic_passed": qc1.passed,
                    "technical_passed": qc2.passed,
                },
            )

            if qc1.passed and qc2.passed:
                final_job = self.jobs.transition(
                    job_id,
                    JobState.COMPLETED,
                    progress=1.0,
                    message="Artwork reconstruction complete",
                    result_path=str(final_path),
                )
                self._log_stage(final_job, "job_completed", stage="completed")
                return final_job

            reasons = [*qc1.reasons, *qc2.reasons]
            final_job = self.jobs.transition(
                job_id,
                JobState.REVIEW_REQUIRED,
                progress=1.0,
                message="Output created — review required",
                failure_category=FailureCategory.QUALITY_FAILURE,
                failure_reason=", ".join(sorted(set(reasons))) or "quality gate failed",
                result_path=str(final_path),
            )
            self._log_stage(
                final_job,
                "job_review_required",
                stage="review_required",
                failure_reason=final_job.failure_reason,
            )
            return final_job
        except StorageLimitExceeded as exc:
            return self.jobs.transition(
                job_id,
                JobState.BLOCKED_BUDGET,
                message="Storage budget blocked processing",
                failure_category=FailureCategory.BUDGET_FAILURE,
                failure_reason=str(exc),
            )
        except Exception as exc:
            logger.exception(
                "job_failed",
                extra={"job_id": job.job_id, "trace_id": job.trace_id, "stage": "pipeline"},
            )
            return self.jobs.transition(
                job_id,
                JobState.FAILED_FINAL,
                message="Artwork reconstruction failed",
                failure_category=FailureCategory.COMPUTE_ERROR,
                failure_reason=str(exc),
            )

    def recover_interrupted_jobs(self) -> list[JobRecord]:
        recovered: list[JobRecord] = []
        for job in self.jobs.list_interrupted():
            if job.state is JobState.BLOCKED_BUDGET:
                continue
            recovered_job = self.jobs.transition(
                job.job_id,
                JobState.RESUMING,
                progress=min(job.progress, 0.95),
                message="Recovered after restart — resuming from checkpoints",
            )
            recovered.append(recovered_job)
            self._log_stage(recovered_job, "job_recovered", stage="recovery")
        return recovered
