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
    ArtworkType,
    BoundingBox,
    DesignSpec,
    ExportProfile,
    FailureCategory,
    JobRecord,
    JobState,
    MaterialSeparationEvidence,
    MultiReferenceAlignmentEvidence,
    MultiReferenceCorrespondenceEvidence,
    MultiReferenceFusionEvidence,
    PreflightResult,
    ProviderAction,
    ProviderRequest,
    ProviderResult,
    QCResult,
    QualityMode,
    RegionConfidenceMapEvidence,
    RegionRescuePlanEvidence,
    RegionReplacementMode,
    RepresentationPlanEvidence,
    RouteDecision,
    RouteKind,
    SuperResolutionReadinessEvidence,
    TextureHandlingEvidence,
    TypographySpec,
)
from .exporter import export_master
from .feature_correspondence import build_feature_correspondence_evidence
from .geometry import (
    GeometryRenderUnavailable,
    geometry_to_svg,
    geometry_topology_evidence,
    render_geometry_master,
)
from .job_store import JobStore
from .font_catalog import get_font_catalog
from .font_matcher import match_typography_fonts, merge_verified_font_matches
from .local_ocr import LocalOCRUnavailable, analyze_artwork_text, available as local_ocr_available
from .logging_config import log_event
from .material_separation import build_material_separation_evidence
from .multi_reference import build_reference_fusion
from .output_readiness import artwork_output_blockers
from .preflight import inspect_image, sha256_file
from .performance import PerformanceStore, StageCache
from .providers import (
    ProviderProtocolError,
    ProviderUnavailable,
    RemoteProvider,
    materialize_provider_candidate,
)
from .qc import semantic_qc, technical_qc
from .qc_policy import load_qc_policy
from .reconstruction import CandidateInfo, normalize_candidate, reconstruct_local_baseline
from .reference_alignment import build_reference_alignment
from .region_evidence import build_region_confidence_map
from .region_rescue import build_region_rescue_plan
from .representation_plan import build_representation_plan
from .resources import capture_resources
from .router import choose_route, forced_route_decision
from .router_policy import load_router_policy
from .settings import Settings
from .storage import StorageLimitExceeded, StorageManager
from .super_resolution import build_super_resolution_readiness
from .texture_handling import build_texture_handling_evidence
from .typography import (
    TypographyRenderUnavailable,
    overlay_typography,
    render_typography_master,
    replace_mixed_typography,
)


logger = logging.getLogger("pod_engine")


class Engine:
    def __init__(
        self,
        settings: Settings,
        *,
        route_override: RouteKind | None = None,
    ) -> None:
        self.settings = settings
        self.route_override = route_override
        settings.ensure_directories()
        self.storage = StorageManager(settings)
        self.jobs = JobStore(settings.database_path)
        self.checkpoints = CheckpointManager(settings.jobs_dir)
        self.stage_cache = StageCache(settings.cache_dir / "stages")
        self.performance = PerformanceStore(settings.database_path)
        self.provider = RemoteProvider(settings)
        self.qc_policy = load_qc_policy(settings.qc_policy_path)
        self.router_policy = load_router_policy(settings.router_policy_path)

    def _job_dir(self, job_id: str) -> Path:
        return self.settings.jobs_dir / job_id

    def _apply_visual_font_matching(
        self,
        job: JobRecord,
        source_path: Path,
        artwork_bbox: BoundingBox,
        typography: TypographySpec,
    ) -> TypographySpec:
        if not self.settings.visual_font_match_enabled or not typography.lines:
            return typography
        try:
            matched = match_typography_fonts(
                source_path,
                artwork_bbox,
                typography,
                self.settings,
            )
        except Exception as exc:
            self._log_stage(
                job,
                "visual_font_match_unavailable",
                stage="analyzing",
                failure_reason=f"{type(exc).__name__}: {exc}",
            )
            return typography

        evidence = [
            line.font_match.model_dump(mode="json")
            for line in matched.lines
            if line.font_match is not None
        ]
        if evidence:
            accepted = sum(bool(item.get("accepted")) for item in evidence)
            self.checkpoints.write(
                job.job_id,
                "font_match",
                {
                    "method": "visual_render_compare_v1",
                    "font_match_confidence": matched.font_match_confidence,
                    "accepted_lines": accepted,
                    "total_lines": len(evidence),
                    "lines": evidence,
                },
            )
            self._log_stage(
                job,
                "visual_font_match_completed",
                stage="analyzing",
                quality_score=matched.font_match_confidence,
            )
        return matched

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
        cache_hit: bool = False,
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
        if cache_hit:
            fields["cache_hit"] = True
        if duration_ms is not None:
            self.performance.record(
                job_id=job.job_id,
                stage=stage,
                event=event,
                duration_ms=duration_ms,
                cache_hit=cache_hit,
            )
        log_event(logger, event, **fields)

    def create_job(
        self, source_paths: list[Path], quality_mode: QualityMode,
        *, force_remote: bool = False, parent_job_id: str | None = None,
    ) -> JobRecord:
        required = sum(path.stat().st_size for path in source_paths if path.exists())
        self.storage.assert_capacity(required)
        job = JobRecord(
            quality_mode=quality_mode,
            source_paths=[str(path) for path in source_paths],
            force_remote=force_remote,
            parent_job_id=parent_job_id,
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
        results: list[PreflightResult] = []
        cache_hits = 0
        for source in map(Path, job.source_paths):
            source_hash = sha256_file(source)
            cached = self.stage_cache.get("preflight", "v1", [source_hash])
            result: PreflightResult | None = None
            if isinstance(cached, dict):
                try:
                    cached_result = PreflightResult.model_validate(cached)
                except Exception:
                    cached_result = None
                if cached_result is not None and cached_result.sha256 == source_hash:
                    result = cached_result
                    cache_hits += 1
            if result is None:
                result = inspect_image(source)
                self.stage_cache.put(
                    "preflight",
                    "v1",
                    [result.sha256],
                    result.model_dump(mode="json"),
                )
            results.append(result)
        self.checkpoints.write(
            job.job_id,
            "preflight",
            [result.model_dump(mode="json") for result in results],
        )
        refreshed = self.jobs.get(job.job_id) or job
        duration_ms = round((time.perf_counter() - started) * 1000)
        self._log_stage(
            refreshed,
            "preflight_completed",
            stage="preflight",
            duration_ms=duration_ms,
            cache_hit=bool(results) and cache_hits == len(results),
        )
        if cache_hits:
            self._log_stage(
                refreshed,
                "preflight_content_cache_reused",
                stage="preflight_cache",
                duration_ms=duration_ms,
                cache_hit=True,
                quality_score=cache_hits / max(1, len(results)),
            )
        return results

    def _reference_fusion(
        self,
        job: JobRecord,
        source_paths: list[Path],
        preflights: list[PreflightResult],
    ) -> MultiReferenceFusionEvidence:
        checkpoint = self.checkpoints.payload(job.job_id, "reference_fusion")
        if isinstance(checkpoint, dict):
            return MultiReferenceFusionEvidence.model_validate(checkpoint)

        fusion = build_reference_fusion(source_paths, preflights)
        self.checkpoints.write(
            job.job_id,
            "reference_fusion",
            fusion.model_dump(mode="json"),
        )
        self._log_stage(
            job,
            "reference_fusion_completed",
            stage="preflight",
            quality_score=fusion.consensus_confidence,
            failure_reason=(
                "high-quality references conflict"
                if fusion.conflict_detected
                else None
            ),
        )
        return fusion

    def _reference_alignment(
        self,
        job: JobRecord,
        preflights: list[PreflightResult],
        fusion: MultiReferenceFusionEvidence,
    ) -> MultiReferenceAlignmentEvidence:
        checkpoint = self.checkpoints.payload(job.job_id, "reference_alignment")
        if isinstance(checkpoint, dict):
            return MultiReferenceAlignmentEvidence.model_validate(checkpoint)

        evidence = build_reference_alignment(preflights, fusion)
        self.checkpoints.write(
            job.job_id,
            "reference_alignment",
            evidence.model_dump(mode="json"),
        )
        self._log_stage(
            job,
            "reference_alignment_completed",
            stage="preflight",
            quality_score=evidence.mean_geometry_confidence,
            failure_reason=(
                ", ".join(evidence.reason_codes)
                if evidence.fail_closed
                else None
            ),
        )
        return evidence

    def _feature_correspondence(
        self,
        job: JobRecord,
        source_paths: list[Path],
        preflights: list[PreflightResult],
        fusion: MultiReferenceFusionEvidence,
        alignment: MultiReferenceAlignmentEvidence,
    ) -> MultiReferenceCorrespondenceEvidence:
        checkpoint = self.checkpoints.payload(job.job_id, "feature_correspondence")
        if isinstance(checkpoint, dict):
            return MultiReferenceCorrespondenceEvidence.model_validate(checkpoint)

        evidence = build_feature_correspondence_evidence(
            source_paths,
            preflights,
            fusion,
            alignment,
        )
        self.checkpoints.write(
            job.job_id,
            "feature_correspondence",
            evidence.model_dump(mode="json"),
        )
        self._log_stage(
            job,
            "feature_correspondence_completed",
            stage="preflight",
            quality_score=evidence.mean_inlier_ratio,
            failure_reason=(
                ", ".join(evidence.reason_codes)
                if evidence.fail_closed
                else None
            ),
        )
        return evidence

    def _region_confidence_map(
        self,
        job: JobRecord,
        source_paths: list[Path],
        preflights: list[PreflightResult],
        fusion: MultiReferenceFusionEvidence,
    ) -> RegionConfidenceMapEvidence:
        checkpoint = self.checkpoints.payload(job.job_id, "region_confidence_map")
        if isinstance(checkpoint, dict):
            return RegionConfidenceMapEvidence.model_validate(checkpoint)

        failure_reason: str | None = None
        try:
            evidence = build_region_confidence_map(
                source_paths,
                preflights,
                fusion,
            )
        except (OSError, ValueError) as exc:
            failure_reason = f"{type(exc).__name__}: {exc}"
            evidence = RegionConfidenceMapEvidence(
                primary_index=fusion.primary_index,
                excluded_indices=[
                    index
                    for index in range(len(source_paths))
                    if index != fusion.primary_index
                ],
                mean_confidence=0.0,
                minimum_confidence=0.0,
                support_coverage=0.0,
                low_confidence_cells=0,
                reason_codes=["region_evidence_unavailable"],
            )

        self.checkpoints.write(
            job.job_id,
            "region_confidence_map",
            evidence.model_dump(mode="json"),
        )
        self._log_stage(
            job,
            "region_confidence_map_completed",
            stage="preflight",
            quality_score=evidence.mean_confidence,
            failure_reason=failure_reason,
        )
        return evidence

    def _region_rescue_plan(
        self,
        job: JobRecord,
        region_map: RegionConfidenceMapEvidence,
        fusion: MultiReferenceFusionEvidence,
    ) -> RegionRescuePlanEvidence:
        checkpoint = self.checkpoints.payload(job.job_id, "region_rescue_plan")
        if isinstance(checkpoint, dict):
            return RegionRescuePlanEvidence.model_validate(checkpoint)

        plan = build_region_rescue_plan(
            region_map,
            fusion,
            provider_available=self.provider.available,
        )
        self.checkpoints.write(
            job.job_id,
            "region_rescue_plan",
            plan.model_dump(mode="json"),
        )
        self._log_stage(
            job,
            "region_rescue_plan_completed",
            stage="preflight",
            quality_score=(
                1.0
                if plan.disposition.value == "none"
                else max(
                    0.0,
                    1.0 - min(1.0, plan.target_cell_count / 16.0),
                )
            ),
            failure_reason=(
                ", ".join(plan.reason_codes)
                if plan.fail_closed
                else None
            ),
        )
        return plan

    def run_preflight(self, job_id: str) -> JobRecord:
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        try:
            preflights = self._perform_preflight(job)
            source_paths = [Path(path) for path in job.source_paths]
            fusion = self._reference_fusion(
                job,
                source_paths,
                preflights,
            )
            alignment = self._reference_alignment(job, preflights, fusion)
            self._feature_correspondence(
                job,
                source_paths,
                preflights,
                fusion,
                alignment,
            )
            region_map = self._region_confidence_map(
                job,
                source_paths,
                preflights,
                fusion,
            )
            self._region_rescue_plan(job, region_map, fusion)
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

    @staticmethod
    def _apply_reference_fusion_constraints(
        design_spec: DesignSpec,
        fusion: MultiReferenceFusionEvidence,
    ) -> DesignSpec:
        if not fusion.conflict_detected:
            return design_spec

        capabilities = list(design_spec.required_capabilities)
        for capability in (
            "need_reference_disambiguation",
            "need_semantic_reconstruction",
        ):
            if capability not in capabilities:
                capabilities.append(capability)
        return design_spec.model_copy(
            update={
                "required_capabilities": sorted(capabilities),
                "confidence": min(
                    design_spec.confidence,
                    fusion.consensus_confidence,
                ),
            }
        )

    def _analyze(
        self,
        job: JobRecord,
        source_paths: list[Path],
        preflights: list[PreflightResult],
        fusion: MultiReferenceFusionEvidence,
    ) -> tuple[DesignSpec, ProviderResult | None]:
        checkpoint = self.checkpoints.payload(job.job_id, "design_spec")
        if isinstance(checkpoint, dict):
            restored = DesignSpec.model_validate(checkpoint)
            return self._apply_reference_fusion_constraints(restored, fusion), None

        self.jobs.transition(
            job.job_id,
            JobState.ANALYZING,
            progress=0.18,
            message="Analyzing artwork structure",
        )
        started = time.perf_counter()
        primary_index = fusion.primary_index
        source_hash = preflights[primary_index].sha256
        local_started = time.perf_counter()
        cached_local = self.stage_cache.get(
            "local_analysis",
            "v1",
            [source_hash],
        )
        local_cache_hit = False
        local_spec: DesignSpec | None = None
        if isinstance(cached_local, dict):
            try:
                local_spec = DesignSpec.model_validate(cached_local)
                local_cache_hit = True
            except Exception:
                local_spec = None
        if local_spec is None:
            local_spec = analyze_locally(
                [source_paths[primary_index]],
                [preflights[primary_index]],
            )
            self.stage_cache.put(
                "local_analysis",
                "v1",
                [source_hash],
                local_spec.model_dump(mode="json"),
            )
        self._log_stage(
            job,
            "local_analysis_completed",
            stage="local_analysis",
            duration_ms=round((time.perf_counter() - local_started) * 1000),
            cache_hit=local_cache_hit,
            quality_score=local_spec.confidence,
        )
        provider_result: ProviderResult | None = None

        local_spec = self._apply_reference_fusion_constraints(
            local_spec,
            fusion,
        )

        should_run_local_ocr = (
            local_spec.artwork_bbox is not None
            and local_ocr_available(self.settings)
            and (
                local_spec.artwork_type
                in {ArtworkType.TYPOGRAPHY, ArtworkType.LOGO, ArtworkType.MIXED}
                or "need_exact_text" in local_spec.required_capabilities
            )
        )
        if should_run_local_ocr:
            try:
                ocr_result = analyze_artwork_text(
                    source_paths[primary_index],
                    local_spec.artwork_bbox,
                    self.settings,
                )
                if (
                    ocr_result.exact_text
                    and ocr_result.typography.line_order_confidence >= 0.75
                ):
                    local_spec.exact_text = list(ocr_result.exact_text)
                    local_spec.typography = self._apply_visual_font_matching(
                        job,
                        source_paths[primary_index],
                        local_spec.artwork_bbox,
                        ocr_result.typography,
                    )
                    if "need_exact_text" not in local_spec.required_capabilities:
                        local_spec.required_capabilities.append("need_exact_text")
                    self.checkpoints.write(
                        job.job_id,
                        "local_ocr",
                        {
                            "backend": ocr_result.backend,
                            "backend_version": ocr_result.backend_version,
                            "backend_source": ocr_result.backend_source,
                            "executable_sha256": ocr_result.executable_sha256,
                            "exact_text": ocr_result.exact_text,
                            "typography": local_spec.typography.model_dump(mode="json"),
                        },
                    )
                    self._log_stage(
                        job,
                        "local_ocr_completed",
                        stage="analyzing",
                    )
            except LocalOCRUnavailable as exc:
                self._log_stage(
                    job,
                    "local_ocr_unavailable",
                    stage="analyzing",
                    failure_reason=str(exc),
                )

        local_ocr_text = list(local_spec.exact_text)
        local_ocr_typography = (
            local_spec.typography
            if local_spec.typography is not None
            and local_spec.typography.evidence_provider.startswith("tesseract")
            else None
        )

        typography_needs_identification = (
            "need_exact_text" in local_spec.required_capabilities
            and (
                not local_spec.exact_text
                or local_spec.typography is None
                or not local_spec.typography.lines
                or local_spec.typography.font_match_confidence < 0.70
                or any(not line.font_family for line in local_spec.typography.lines)
            )
        )
        needs_remote_analysis = self.provider.available and (
            job.quality_mode is QualityMode.QUICK_2D
            or "need_semantic_reconstruction" in local_spec.required_capabilities
            or typography_needs_identification
            or local_spec.confidence < 0.55
            or job.quality_mode is QualityMode.MAX_FIDELITY
        )

        if needs_remote_analysis:
            try:
                analysis_capabilities = ["need_design_spec"]
                if "need_reference_disambiguation" in local_spec.required_capabilities:
                    analysis_capabilities.append("need_reference_disambiguation")
                analysis_request = ProviderRequest(
                    action=ProviderAction.ANALYZE,
                    job_id=job.job_id,
                    quality_mode=job.quality_mode,
                    source_paths=[str(path) for path in source_paths],
                    design_spec=local_spec,
                    requested_capabilities=analysis_capabilities,
                )
                recipe = self.provider.recipe()
                provider_cache_options = {
                    "quality_mode": job.quality_mode.value,
                    "provider_url": self.settings.remote_provider_url,
                    "provider_name": self.settings.remote_provider_name,
                    "recipe_id": recipe.recipe_id,
                    "recipe_version": recipe.version,
                    "requested_capabilities": sorted(analysis_capabilities),
                }
                provider_started = time.perf_counter()
                cached_provider = self.stage_cache.get(
                    "provider_analyze",
                    "v1",
                    [item.sha256 for item in preflights],
                    provider_cache_options,
                )
                provider_cache_hit = False
                if isinstance(cached_provider, dict):
                    try:
                        provider_result = ProviderResult.model_validate(cached_provider)
                        provider_cache_hit = True
                    except Exception:
                        provider_result = None
                if provider_result is None:
                    provider_result = self.provider.execute(analysis_request)
                    self.stage_cache.put(
                        "provider_analyze",
                        "v1",
                        [item.sha256 for item in preflights],
                        provider_result.model_dump(mode="json"),
                        provider_cache_options,
                    )
                self._log_stage(
                    job,
                    "provider_analysis_completed",
                    stage="provider_analysis",
                    duration_ms=round((time.perf_counter() - provider_started) * 1000),
                    provider=provider_result.provider,
                    model_version=provider_result.model_version,
                    cache_hit=provider_cache_hit,
                )
                if provider_result.design_spec is not None:
                    local_spec = provider_result.design_spec
                    if not local_spec.exact_text and local_ocr_text:
                        local_spec.exact_text = list(local_ocr_text)
                    if (
                        (local_spec.typography is None or not local_spec.typography.lines)
                        and local_ocr_typography is not None
                    ):
                        local_spec.typography = local_ocr_typography
                    elif (
                        local_spec.typography is not None
                        and local_ocr_typography is not None
                    ):
                        local_spec.typography = merge_verified_font_matches(
                            local_spec.typography,
                            local_ocr_typography,
                        )
                if (
                    local_spec.artwork_bbox is not None
                    and local_spec.typography is not None
                    and local_spec.typography.lines
                    and self.settings.visual_font_match_enabled
                    and any(
                        line.font_match is None or not line.font_match.accepted
                        for line in local_spec.typography.lines
                    )
                ):
                    local_spec.typography = self._apply_visual_font_matching(
                        job,
                        source_paths[primary_index],
                        local_spec.artwork_bbox,
                        local_spec.typography,
                    )
                if (
                    local_spec.typography is not None
                    and local_spec.typography.lines
                    and any(line.font_family for line in local_spec.typography.lines)
                ):
                    catalog = get_font_catalog(self.settings)
                    normalized_lines = [
                        line.model_copy(
                            update={
                                "font_family": catalog.normalize_known_family(
                                    line.font_family
                                )
                                if line.font_family
                                else ""
                            }
                        )
                        for line in local_spec.typography.lines
                    ]
                    local_spec.typography = local_spec.typography.model_copy(
                        update={"lines": normalized_lines}
                    )
                self.checkpoints.write(
                    job.job_id,
                    "analysis_provider",
                    {
                        "provider": provider_result.provider,
                        "model_version": provider_result.model_version,
                    },
                )
                if provider_result.recognized_text and not local_spec.exact_text:
                    local_spec.exact_text = list(provider_result.recognized_text)
                if (
                    local_spec.typography is not None
                    and local_spec.typography.lines
                    and not local_spec.exact_text
                ):
                    local_spec.exact_text = [
                        line.text for line in local_spec.typography.lines
                    ]
            except (ProviderUnavailable, ProviderProtocolError) as exc:
                self._log_stage(
                    job,
                    "semantic_analyzer_fallback",
                    stage="analyzing",
                    failure_reason=str(exc),
                )

        local_spec = self._apply_reference_fusion_constraints(
            local_spec,
            fusion,
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

    def _material_separation_evidence(
        self,
        job: JobRecord,
        source_path: Path,
        preflight: PreflightResult,
        design_spec: DesignSpec,
        *,
        primary_index: int,
    ) -> MaterialSeparationEvidence:
        checkpoint = self.checkpoints.payload(job.job_id, "material_separation")
        if isinstance(checkpoint, dict):
            return MaterialSeparationEvidence.model_validate(checkpoint)

        evidence = build_material_separation_evidence(
            source_path,
            preflight,
            design_spec,
            primary_index=primary_index,
        )
        self.checkpoints.write(
            job.job_id,
            "material_separation",
            evidence.model_dump(mode="json"),
        )
        self._log_stage(
            job,
            "material_separation_evidence_completed",
            stage="analyzing",
            quality_score=evidence.confidence,
            failure_reason=(
                ", ".join(evidence.reason_codes)
                if evidence.fail_closed
                else None
            ),
        )
        return evidence

    def _texture_handling_evidence(
        self,
        job: JobRecord,
        source_path: Path,
        preflight: PreflightResult,
        design_spec: DesignSpec,
        material: MaterialSeparationEvidence,
        region_map: RegionConfidenceMapEvidence,
        *,
        primary_index: int,
    ) -> TextureHandlingEvidence:
        checkpoint = self.checkpoints.payload(job.job_id, "texture_handling")
        if isinstance(checkpoint, dict):
            return TextureHandlingEvidence.model_validate(checkpoint)

        evidence = build_texture_handling_evidence(
            source_path,
            preflight,
            design_spec,
            material,
            region_map,
            primary_index=primary_index,
        )
        self.checkpoints.write(
            job.job_id,
            "texture_handling",
            evidence.model_dump(mode="json"),
        )
        self._log_stage(
            job,
            "texture_handling_evidence_completed",
            stage="analyzing",
            quality_score=evidence.confidence,
            failure_reason=(
                ", ".join(evidence.reason_codes)
                if evidence.fail_closed
                else None
            ),
        )
        return evidence

    def _super_resolution_readiness(
        self,
        job: JobRecord,
        preflight: PreflightResult,
        design_spec: DesignSpec,
        material: MaterialSeparationEvidence,
        texture: TextureHandlingEvidence,
        region_map: RegionConfidenceMapEvidence,
        profile: ExportProfile,
        *,
        primary_index: int,
    ) -> SuperResolutionReadinessEvidence:
        checkpoint = self.checkpoints.payload(job.job_id, "super_resolution_readiness")
        if isinstance(checkpoint, dict):
            return SuperResolutionReadinessEvidence.model_validate(checkpoint)

        evidence = build_super_resolution_readiness(
            preflight,
            design_spec,
            material,
            texture,
            region_map,
            profile=profile,
            required_native_long_edge=(
                self.qc_policy.for_mode(job.quality_mode).required_native_long_edge
            ),
            primary_index=primary_index,
            provider_available=self.provider.available,
        )
        self.checkpoints.write(
            job.job_id,
            "super_resolution_readiness",
            evidence.model_dump(mode="json"),
        )
        self._log_stage(
            job,
            "super_resolution_readiness_completed",
            stage="analyzing",
            quality_score=evidence.confidence,
            failure_reason=(
                ", ".join(evidence.reason_codes)
                if evidence.fail_closed
                else None
            ),
        )
        return evidence

    def _representation_plan(
        self,
        job: JobRecord,
        design_spec: DesignSpec,
    ) -> RepresentationPlanEvidence:
        checkpoint = self.checkpoints.payload(job.job_id, "representation_plan")
        if isinstance(checkpoint, dict):
            return RepresentationPlanEvidence.model_validate(checkpoint)

        plan = build_representation_plan(design_spec)
        self.checkpoints.write(
            job.job_id,
            "representation_plan",
            plan.model_dump(mode="json"),
        )
        self._log_stage(
            job,
            "representation_plan_completed",
            stage="analyzing",
            quality_score=plan.confidence,
            failure_reason=(
                ", ".join(plan.missing_capabilities)
                if plan.fail_closed
                else None
            ),
        )
        return plan

    def _route(self, job: JobRecord, design_spec: DesignSpec) -> RouteDecision:
        checkpoint = self.checkpoints.payload(job.job_id, "route")
        if isinstance(checkpoint, dict):
            return RouteDecision.model_validate(checkpoint)

        if job.force_remote:
            if not self.provider.available:
                raise ProviderUnavailable("AI retry requires an available provider")
            decision = RouteDecision(
                route=RouteKind.REMOTE_SEMANTIC,
                required_capabilities=sorted(set(design_spec.required_capabilities) | {"need_semantic_reconstruction"}),
                reason_codes=["explicit_user_ai_retry"],
                use_remote_provider=True,
                deterministic_finish=False,
            )
        elif self.route_override is not None:
            decision = forced_route_decision(
                design_spec,
                self.route_override,
                remote_available=self.provider.available,
            )
        else:
            decision = choose_route(
                design_spec,
                job.quality_mode,
                remote_available=self.provider.available,
                policy=self.router_policy,
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
        fusion: MultiReferenceFusionEvidence,
    ) -> tuple[CandidateInfo, ProviderResult | None, bool, list[str]]:
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
                return (
                    info,
                    restored_provider,
                    bool(candidate_checkpoint.get("used_remote")),
                    list(candidate_checkpoint.get("recognized_text") or []),
                )

        self.jobs.transition(
            job.job_id,
            JobState.RECONSTRUCTING,
            progress=0.42,
            message="Reconstructing clean 2D artwork",
        )
        started = time.perf_counter()
        provider_result: ProviderResult | None = None
        used_remote = False
        recognized_text: list[str] = []
        precision_ops: list[str] = []
        if (
            design_spec.typography is not None
            and design_spec.typography.lines
            and design_spec.typography.line_order_confidence >= 0.75
            and design_spec.exact_text
        ):
            evidence_text = [line.text for line in design_spec.typography.lines]
            if evidence_text == design_spec.exact_text:
                recognized_text = list(evidence_text)
        temp_dir = self._job_dir(job.job_id) / "temp"
        primary_source = source_paths[fusion.primary_index]
        if fusion.reference_count > 1:
            precision_ops.append("multi_reference_fusion")

        harness_force_remote = self.route_override in {
            RouteKind.REMOTE_SEMANTIC,
            RouteKind.HYBRID,
        }
        deterministic_typography = (
            not job.force_remote
            and route.deterministic_finish
            and not harness_force_remote
            and design_spec.artwork_type is ArtworkType.TYPOGRAPHY
            and design_spec.typography is not None
            and bool(design_spec.typography.lines)
        )
        deterministic_geometry = (
            not job.force_remote
            and route.deterministic_finish
            and not harness_force_remote
            and design_spec.artwork_type is ArtworkType.LOGO
            and design_spec.geometry is not None
            and bool(design_spec.geometry.primitives)
        )
        deterministic_complete = False
        deterministic_attempt_failed = False

        if deterministic_geometry:
            try:
                rendered_path = render_geometry_master(
                    design_spec.geometry,
                    temp_dir / "deterministic-geometry.png",
                )
                if design_spec.exact_text:
                    if design_spec.typography is None or not design_spec.typography.lines:
                        raise GeometryRenderUnavailable(
                            "logo exact text requires typography evidence"
                        )
                    rendered_path, recognized_text = overlay_typography(
                        rendered_path,
                        design_spec.typography,
                        self.settings,
                        temp_dir / "deterministic-logo.png",
                    )
                geometry_svg = geometry_to_svg(
                    design_spec.geometry,
                    self._job_dir(job.job_id) / "master" / "vector" / "geometry.svg",
                )
                topology = geometry_topology_evidence(design_spec.geometry)
                self.checkpoints.write(
                    job.job_id,
                    "geometry_topology",
                    {
                        **topology.model_dump(mode="json"),
                        "svg_sha256": sha256_file(geometry_svg),
                    },
                )
                candidate = normalize_candidate(
                    rendered_path,
                    temp_dir / "candidate.png",
                    source_path=primary_source,
                )
                deterministic_complete = True
                precision_ops.append("deterministic_geometry")
                self._log_stage(
                    job,
                    "deterministic_geometry_rendered",
                    stage="reconstructing",
                    route=route.route.value,
                )
            except (GeometryRenderUnavailable, TypographyRenderUnavailable) as exc:
                deterministic_attempt_failed = True
                self._log_stage(
                    job,
                    "deterministic_geometry_unavailable",
                    stage="reconstructing",
                    failure_reason=str(exc),
                    route=route.route.value,
                )

        if not deterministic_complete and deterministic_typography:
            try:
                rendered_path = render_typography_master(
                    design_spec.typography,
                    self.settings,
                    temp_dir / "deterministic-typography.png",
                )
                candidate = normalize_candidate(
                    rendered_path,
                    temp_dir / "candidate.png",
                    source_path=primary_source,
                )
                recognized_text = [
                    line.text for line in design_spec.typography.lines
                ]
                deterministic_complete = True
                precision_ops.append("deterministic_typography")
                self._log_stage(
                    job,
                    "deterministic_typography_rendered",
                    stage="reconstructing",
                    route=route.route.value,
                )
            except TypographyRenderUnavailable as exc:
                deterministic_attempt_failed = True
                self._log_stage(
                    job,
                    "deterministic_typography_unavailable",
                    stage="reconstructing",
                    failure_reason=str(exc),
                    route=route.route.value,
                )

        use_remote_reconstruction = (
            self.provider.available
            and (route.use_remote_provider or deterministic_attempt_failed)
        )
        if not deterministic_complete and use_remote_reconstruction:
            try:
                provider_result = self.provider.execute(
                    ProviderRequest(
                        action=ProviderAction.RECONSTRUCT,
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
                        source_path=primary_source,
                    )
                    used_remote = True
                    precision_ops.append("remote_reconstruction")
                    recognized_text = list(provider_result.recognized_text)
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
                    primary_index=fusion.primary_index,
                )
                precision_ops.append("local_baseline")
        elif not deterministic_complete:
            candidate = reconstruct_local_baseline(
                source_paths,
                preflights,
                design_spec,
                temp_dir / "candidate.png",
                primary_index=fusion.primary_index,
            )
            precision_ops.append("local_baseline")

        if (
            design_spec.artwork_type is ArtworkType.MIXED
            and design_spec.typography is not None
            and design_spec.typography.lines
        ):
            try:
                refined_path, replaced_text = replace_mixed_typography(
                    candidate.path,
                    design_spec.typography,
                    self.settings,
                    temp_dir / "mixed-text-refined.png",
                )
                candidate = normalize_candidate(
                    refined_path,
                    temp_dir / "candidate-precision.png",
                    source_path=candidate.source_path,
                )
                expected = list(design_spec.exact_text)
                if expected and replaced_text == expected:
                    recognized_text = expected
                else:
                    for text_value in replaced_text:
                        if text_value not in recognized_text:
                            recognized_text.append(text_value)
                precision_ops.append("mixed_typography")
                repaired_lines = [
                    line
                    for line in design_spec.typography.lines
                    if line.replacement_mode is RegionReplacementMode.REPAIR_LOCAL
                    and line.text in replaced_text
                ]
                if repaired_lines:
                    precision_ops.append("local_text_repair")
                    self.checkpoints.write(
                        job.job_id,
                        "local_text_repair",
                        {
                            "method": "directional_boundary_interpolation_v1",
                            "deterministic": True,
                            "min_confidence": self.settings.local_text_repair_min_confidence,
                            "repaired_lines": [
                                {
                                    "text": line.text,
                                    "confidence": line.replacement_confidence,
                                    "mask_points": len(line.replacement_mask),
                                }
                                for line in repaired_lines
                            ],
                        },
                    )
                self._log_stage(
                    job,
                    "mixed_typography_refined",
                    stage="reconstructing",
                    route=route.route.value,
                )
            except TypographyRenderUnavailable as exc:
                self._log_stage(
                    job,
                    "mixed_typography_skipped",
                    stage="reconstructing",
                    failure_reason=str(exc),
                    route=route.route.value,
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
                "recognized_text": recognized_text,
                "precision_ops": sorted(set(precision_ops)),
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
        return candidate, provider_result, used_remote, recognized_text

    def _judge_semantics(
        self,
        job: JobRecord,
        source_paths: list[Path],
        candidate: CandidateInfo,
        design_spec: DesignSpec,
    ) -> ProviderResult | None:
        checkpoint = self.checkpoints.payload(job.job_id, "semantic_judge")
        if isinstance(checkpoint, dict):
            try:
                restored = ProviderResult.model_validate(checkpoint)
            except ValueError:
                restored = None
            if restored is not None and restored.judge_result is not None:
                return restored

        if not self.provider.available or job.quality_mode is QualityMode.QUICK_2D:
            return None

        should_judge = (
            job.quality_mode is QualityMode.MAX_FIDELITY
            or "need_semantic_reconstruction" in design_spec.required_capabilities
            or bool(design_spec.exact_text)
        )
        if not should_judge:
            return None

        started = time.perf_counter()
        try:
            result = self.provider.execute(
                ProviderRequest(
                    action=ProviderAction.JUDGE,
                    job_id=job.job_id,
                    quality_mode=job.quality_mode,
                    source_paths=[str(path) for path in source_paths],
                    candidate_path=str(candidate.path),
                    design_spec=design_spec,
                    requested_capabilities=[
                        "judge_exact_text",
                        "judge_layout",
                        "judge_object_fidelity",
                        "judge_color",
                        "judge_texture",
                        "judge_missing_detail",
                    ],
                )
            )
            if result.judge_result is None:
                raise ProviderProtocolError("provider returned no semantic judge result")
            self.checkpoints.write(
                job.job_id,
                "semantic_judge",
                {
                    "provider": result.provider,
                    "model_version": result.model_version,
                    "recognized_text": result.recognized_text,
                    "judge_result": result.judge_result.model_dump(mode="json"),
                },
            )
            self._log_stage(
                job,
                "semantic_judge_completed",
                stage="qc_semantic",
                duration_ms=round((time.perf_counter() - started) * 1000),
                provider=result.provider,
                model_version=result.model_version,
                quality_score=(
                    result.judge_result.object_fidelity
                    if result.judge_result.object_fidelity is not None
                    else result.judge_result.confidence
                ),
            )
            return result
        except (ProviderUnavailable, ProviderProtocolError) as exc:
            self._log_stage(
                job,
                "semantic_judge_fallback",
                stage="qc_semantic",
                failure_reason=str(exc),
            )
            return None

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
        artifacts = [
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
        ]
        geometry_svg = self._job_dir(job.job_id) / "master" / "vector" / "geometry.svg"
        if geometry_svg.is_file():
            artifacts.append(
                ArtifactRef(
                    kind="geometry_svg",
                    path=str(geometry_svg),
                    sha256=sha256_file(geometry_svg),
                    size_bytes=geometry_svg.stat().st_size,
                )
            )

        manifest = ArtifactManifest(
            job_id=job.job_id,
            source_hashes=[item.sha256 for item in preflights],
            artifacts=artifacts,
            model_versions=(
                {provider_result.provider: provider_result.model_version}
                if provider_result and provider_result.model_version
                else {}
            ),
            policy_version=(
                f"router={self.router_policy.policy_id}:{self.router_policy.version};"
                f"qc={self.qc_policy.policy_id}:{self.qc_policy.version}"
            ),
            export_profile="default_pod",
            precision_evidence={
                "reference_fusion": (
                    self.checkpoints.payload(job.job_id, "reference_fusion")
                    or {}
                ),
                "reference_alignment": (
                    self.checkpoints.payload(job.job_id, "reference_alignment")
                    or {}
                ),
                "feature_correspondence": (
                    self.checkpoints.payload(job.job_id, "feature_correspondence")
                    or {}
                ),
                "region_confidence_map": (
                    self.checkpoints.payload(job.job_id, "region_confidence_map")
                    or {}
                ),
                "region_rescue_plan": (
                    self.checkpoints.payload(job.job_id, "region_rescue_plan")
                    or {}
                ),
                "representation_plan": (
                    self.checkpoints.payload(job.job_id, "representation_plan")
                    or {}
                ),
                "material_separation": (
                    self.checkpoints.payload(job.job_id, "material_separation")
                    or {}
                ),
                "texture_handling": (
                    self.checkpoints.payload(job.job_id, "texture_handling")
                    or {}
                ),
                "super_resolution_readiness": (
                    self.checkpoints.payload(
                        job.job_id,
                        "super_resolution_readiness",
                    )
                    or {}
                ),
                "local_ocr": (
                    self.checkpoints.payload(job.job_id, "local_ocr")
                    or {}
                ),
                "font_match": (
                    self.checkpoints.payload(job.job_id, "font_match")
                    or {}
                ),
                "geometry_topology": (
                    self.checkpoints.payload(job.job_id, "geometry_topology")
                    or {}
                ),
                "local_text_repair": (
                    self.checkpoints.payload(job.job_id, "local_text_repair")
                    or {}
                ),
            },
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

        pipeline_started = time.perf_counter()
        try:
            source_paths = [Path(path) for path in job.source_paths]
            preflights = self._perform_preflight(job)
            fusion = self._reference_fusion(job, source_paths, preflights)
            alignment = self._reference_alignment(job, preflights, fusion)
            self._feature_correspondence(
                job,
                source_paths,
                preflights,
                fusion,
                alignment,
            )
            region_map = self._region_confidence_map(
                job,
                source_paths,
                preflights,
                fusion,
            )
            self._region_rescue_plan(job, region_map, fusion)
            job = self.jobs.get(job_id) or job

            profile = ExportProfile()
            estimated_working_bytes = profile.width * profile.height * 4 * 2
            self.storage.assert_capacity(estimated_working_bytes)

            design_spec, analysis_provider = self._analyze(
                job,
                source_paths,
                preflights,
                fusion,
            )
            self._representation_plan(job, design_spec)
            material_evidence = self._material_separation_evidence(
                job,
                source_paths[fusion.primary_index],
                preflights[fusion.primary_index],
                design_spec,
                primary_index=fusion.primary_index,
            )
            texture_evidence = self._texture_handling_evidence(
                job,
                source_paths[fusion.primary_index],
                preflights[fusion.primary_index],
                design_spec,
                material_evidence,
                region_map,
                primary_index=fusion.primary_index,
            )
            self._super_resolution_readiness(
                job,
                preflights[fusion.primary_index],
                design_spec,
                material_evidence,
                texture_evidence,
                region_map,
                profile,
                primary_index=fusion.primary_index,
            )
            job = self.jobs.get(job_id) or job
            route = self._route(job, design_spec)

            (
                candidate,
                reconstruction_provider,
                used_remote,
                recognized_text,
            ) = self._reconstruct(
                job,
                source_paths,
                preflights,
                design_spec,
                route,
                fusion,
            )
            output_blockers = artwork_output_blockers(
                design_spec,
                material_evidence,
                candidate,
                used_remote=used_remote,
            )
            self.checkpoints.write(
                job_id,
                "output_readiness",
                {
                    "print_artwork_isolated": not output_blockers,
                    "blockers": output_blockers,
                    "candidate_is_local_baseline": candidate.local_baseline,
                    "remote_reconstruction_used": used_remote,
                },
            )
            if output_blockers:
                final_job = self.jobs.transition(
                    job_id,
                    JobState.REVIEW_REQUIRED,
                    progress=1.0,
                    message="No 2D artwork created — mockup requires semantic reconstruction",
                    failure_category=FailureCategory.QUALITY_FAILURE,
                    failure_reason=", ".join(output_blockers),
                )
                self._log_stage(
                    final_job,
                    "mockup_output_blocked",
                    stage="job_total",
                    duration_ms=round((time.perf_counter() - pipeline_started) * 1000),
                    failure_reason=final_job.failure_reason,
                )
                return final_job

            judge_provider = self._judge_semantics(
                job,
                source_paths,
                candidate,
                design_spec,
            )
            provider_result = (
                reconstruction_provider
                or analysis_provider
                or judge_provider
            )

            self.jobs.transition(
                job_id,
                JobState.QC,
                progress=0.60,
                message="Checking source fidelity",
            )
            if (
                not recognized_text
                and judge_provider is not None
                and judge_provider.recognized_text
            ):
                recognized_text = list(judge_provider.recognized_text)
            qc1 = semantic_qc(
                design_spec,
                job.quality_mode,
                recognized_text=recognized_text,
                used_remote_provider=used_remote,
                judge_result=(
                    judge_provider.judge_result
                    if judge_provider is not None
                    else None
                ),
                policy=self.qc_policy,
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
                policy=self.qc_policy,
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
                self._log_stage(
                    final_job,
                    "job_completed",
                    stage="job_total",
                    duration_ms=round((time.perf_counter() - pipeline_started) * 1000),
                )
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
                stage="job_total",
                duration_ms=round((time.perf_counter() - pipeline_started) * 1000),
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
