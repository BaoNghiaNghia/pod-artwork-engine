from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from . import __version__
from .contracts import DesignSpec, JobState, ProviderResult, QualityMode, RegionReplacementMode, RouteDecision, RouteKind
from .engine import Engine
from .harness import HarnessCaseFactory, HarnessRunner, HarnessStore
from .harness_models import (
    BenchmarkRecipe,
    BenchmarkScorecard,
    BenchmarkTier,
    CandidateManifest,
    CandidateManifestEntry,
    OperationalMetrics,
    PrecisionEvidence,
    RouteEvidence,
    RunProvenance,
    RuntimeQCEvidence,
)
from .settings import Settings


class HarnessEngineRunner:
    """Execute the real production engine against versioned benchmark cases.

    The Harness remains an evaluator: this runner only materializes candidate
    outputs and operational/precision evidence, then delegates scoring to the
    existing HarnessRunner.
    """

    def __init__(
        self,
        settings: Settings,
        registry,
        store: HarnessStore,
    ) -> None:
        self.base_settings = settings
        self.settings = settings
        self.engine = Engine(settings)
        self.registry = registry
        self.store = store
        self.case_factory = HarnessCaseFactory(registry)

    def _settings_for_recipe(
        self,
        recipe: BenchmarkRecipe,
        recipe_base: Path | None,
    ) -> Settings:
        metadata = recipe.metadata
        overrides: dict[str, object] = {}

        provider_recipe = metadata.get("provider_recipe_path")
        if isinstance(provider_recipe, str) and provider_recipe.strip():
            path = Path(provider_recipe).expanduser()
            if not path.is_absolute() and recipe_base is not None:
                path = recipe_base / path
            overrides["provider_recipe_path"] = path.resolve()

        qc_policy = metadata.get("qc_policy_path")
        if isinstance(qc_policy, str) and qc_policy.strip():
            path = Path(qc_policy).expanduser()
            if not path.is_absolute() and recipe_base is not None:
                path = recipe_base / path
            overrides["qc_policy_path"] = path.resolve()

        router_policy = metadata.get("router_policy_path")
        if isinstance(router_policy, str) and router_policy.strip():
            path = Path(router_policy).expanduser()
            if not path.is_absolute() and recipe_base is not None:
                path = recipe_base / path
            overrides["router_policy_path"] = path.resolve()

        local_ocr = metadata.get("local_ocr_enabled")
        if isinstance(local_ocr, bool):
            overrides["local_ocr_enabled"] = local_ocr

        visual_font_match = metadata.get("visual_font_match_enabled")
        if isinstance(visual_font_match, bool):
            overrides["visual_font_match_enabled"] = visual_font_match

        for key in (
            "visual_font_match_min_score",
            "visual_font_match_min_margin",
        ):
            value = metadata.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric = float(value)
                if not 0 <= numeric <= 1:
                    raise ValueError(f"{key} must be between 0 and 1")
                overrides[key] = numeric

        max_candidates = metadata.get("visual_font_match_max_candidates")
        if isinstance(max_candidates, int) and not isinstance(max_candidates, bool):
            if not 8 <= max_candidates <= 512:
                raise ValueError(
                    "visual_font_match_max_candidates must be between 8 and 512"
                )
            overrides["visual_font_match_max_candidates"] = max_candidates

        ocr_language = metadata.get("tesseract_language")
        if isinstance(ocr_language, str) and ocr_language.strip():
            overrides["tesseract_language"] = ocr_language.strip()

        return replace(self.base_settings, **overrides)

    def _route_override_for_recipe(
        self,
        recipe: BenchmarkRecipe,
    ) -> RouteKind | None:
        raw = recipe.metadata.get("harness_route_override")
        if raw is None or raw == "":
            return None
        if not isinstance(raw, str):
            raise ValueError("harness_route_override must be a route string")
        try:
            return RouteKind(raw)
        except ValueError as exc:
            raise ValueError(
                f"unsupported harness_route_override: {raw}"
            ) from exc

    def _provider_call_count(self, job_id: str) -> int:
        count = 0
        if self.engine.checkpoints.payload(job_id, "analysis_provider"):
            count += 1
        candidate = self.engine.checkpoints.payload(job_id, "candidate")
        if isinstance(candidate, dict) and candidate.get("used_remote"):
            count += 1
        if self.engine.checkpoints.payload(job_id, "semantic_judge"):
            count += 1
        return count

    def _precision_evidence(self, job_id: str) -> PrecisionEvidence:
        local_ocr = self.engine.checkpoints.payload(job_id, "local_ocr")
        font_match = self.engine.checkpoints.payload(job_id, "font_match")
        geometry_topology = self.engine.checkpoints.payload(
            job_id,
            "geometry_topology",
        )
        candidate = self.engine.checkpoints.payload(job_id, "candidate")
        design_spec = self.engine.checkpoints.payload(job_id, "design_spec")
        precision_ops = (
            list(candidate.get("precision_ops") or [])
            if isinstance(candidate, dict)
            else []
        )

        masked_regions = 0
        if isinstance(design_spec, dict):
            typography = design_spec.get("typography")
            if isinstance(typography, dict):
                for line in typography.get("lines") or []:
                    if (
                        isinstance(line, dict)
                        and line.get("replacement_mode")
                        == RegionReplacementMode.REPLACE_MASK.value
                    ):
                        masked_regions += 1

        recipe_id = ""
        if self.engine.provider.available or self.settings.provider_recipe_path is not None:
            try:
                recipe_id = self.engine.provider.recipe().recipe_id
            except Exception:
                recipe_id = ""

        font_lines = (
            list(font_match.get("lines") or [])
            if isinstance(font_match, dict)
            else []
        )
        matched_font_lines = sum(
            bool(line.get("accepted"))
            for line in font_lines
            if isinstance(line, dict)
        )

        return PrecisionEvidence(
            local_ocr=isinstance(local_ocr, dict) and bool(local_ocr.get("exact_text")),
            ocr_backend=(
                str(local_ocr.get("backend") or "")
                if isinstance(local_ocr, dict)
                else ""
            ),
            visual_font_match=matched_font_lines > 0,
            matched_font_lines=matched_font_lines,
            typography_rebuilt="deterministic_typography" in precision_ops,
            mixed_text_refined="mixed_typography" in precision_ops,
            geometry_vector=(
                "deterministic_geometry" in precision_ops
                and (
                    self.settings.jobs_dir
                    / job_id
                    / "master"
                    / "vector"
                    / "geometry.svg"
                ).is_file()
            ),
            compound_geometry=(
                isinstance(geometry_topology, dict)
                and int(geometry_topology.get("compound_path_count") or 0) > 0
            ),
            geometry_subpaths=(
                int(geometry_topology.get("subpath_count") or 0)
                if isinstance(geometry_topology, dict)
                else 0
            ),
            evenodd_compound_fills=(
                int(
                    geometry_topology.get("evenodd_compound_fill_count")
                    or 0
                )
                if isinstance(geometry_topology, dict)
                else 0
            ),
            masked_text_regions=masked_regions,
            provider_recipe_id=recipe_id,
            precision_ops=sorted(set(precision_ops)),
        )

    def _route_evidence(self, job_id: str) -> RouteEvidence:
        route_payload = self.engine.checkpoints.payload(job_id, "route")
        design_payload = self.engine.checkpoints.payload(job_id, "design_spec")
        candidate_payload = self.engine.checkpoints.payload(job_id, "candidate")

        route = None
        if isinstance(route_payload, dict):
            try:
                route = RouteDecision.model_validate(route_payload)
            except ValueError:
                route = None

        design = None
        if isinstance(design_payload, dict):
            try:
                design = DesignSpec.model_validate(design_payload)
            except ValueError:
                design = None

        return RouteEvidence(
            selected_route=route.route if route is not None else None,
            requested_override=self.engine.route_override,
            used_remote=(
                bool(candidate_payload.get("used_remote"))
                if isinstance(candidate_payload, dict)
                else False
            ),
            remote_available=self.engine.provider.available,
            design_confidence=design.confidence if design is not None else None,
            artwork_type=design.artwork_type if design is not None else None,
            required_capabilities=(
                list(design.required_capabilities)
                if design is not None
                else []
            ),
            reason_codes=list(route.reason_codes) if route is not None else [],
        )

    def _runtime_qc_evidence(self, job_id: str) -> RuntimeQCEvidence:
        semantic = self.engine.checkpoints.payload(job_id, "qc_semantic")
        technical = self.engine.checkpoints.payload(job_id, "qc_technical")

        semantic_metrics = (
            semantic.get("metrics")
            if isinstance(semantic, dict) and isinstance(semantic.get("metrics"), dict)
            else {}
        )
        technical_metrics = (
            technical.get("metrics")
            if isinstance(technical, dict) and isinstance(technical.get("metrics"), dict)
            else {}
        )

        def as_float(value: object) -> float | None:
            if isinstance(value, bool) or value is None:
                return None
            if isinstance(value, (int, float)):
                return max(0.0, min(1.0, float(value)))
            return None

        return RuntimeQCEvidence(
            semantic_score=as_float(semantic.get("score")) if isinstance(semantic, dict) else None,
            technical_score=as_float(technical.get("score")) if isinstance(technical, dict) else None,
            semantic_passed=(
                bool(semantic.get("passed"))
                if isinstance(semantic, dict) and "passed" in semantic
                else None
            ),
            technical_passed=(
                bool(technical.get("passed"))
                if isinstance(technical, dict) and "passed" in technical
                else None
            ),
            object_fidelity=as_float(semantic_metrics.get("object_fidelity")),
            resolution_score=as_float(technical_metrics.get("resolution_score")),
            analysis_confidence=as_float(semantic_metrics.get("analysis_confidence")),
        )

    def _candidate_entry(
        self,
        job_id: str,
        elapsed_ms: float,
    ) -> CandidateManifestEntry:
        job = self.engine.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)

        candidate = self.engine.checkpoints.payload(job_id, "candidate")
        recognized_text = (
            list(candidate.get("recognized_text") or [])
            if isinstance(candidate, dict)
            else []
        )

        judge_payload = self.engine.checkpoints.payload(job_id, "semantic_judge")
        judge = None
        if isinstance(judge_payload, dict):
            try:
                provider_result = ProviderResult.model_validate(judge_payload)
                judge = provider_result.judge_result
            except ValueError:
                judge = None

        result_path = job.result_path or str(
            self.settings.jobs_dir / job_id / "final" / "4500x5400.png"
        )
        return CandidateManifestEntry(
            result_path=result_path,
            recognized_text=recognized_text,
            semantic_judge=judge,
            operational=OperationalMetrics(
                latency_ms=max(0.0, elapsed_ms),
                provider_calls=self._provider_call_count(job_id),
                manual_review=job.state is JobState.REVIEW_REQUIRED,
            ),
            precision=self._precision_evidence(job_id),
            route=self._route_evidence(job_id),
            runtime_qc=self._runtime_qc_evidence(job_id),
            metadata={
                "job_id": job_id,
                "job_state": job.state.value,
                "failure_category": (
                    job.failure_category.value if job.failure_category else None
                ),
                "failure_reason": job.failure_reason,
            },
        )

    def run(
        self,
        dataset_id: str,
        tier: BenchmarkTier,
        recipe: BenchmarkRecipe,
        *,
        quality_mode: QualityMode = QualityMode.PRINT_READY,
        limit: int | None = None,
        recipe_base: Path | None = None,
    ) -> BenchmarkScorecard:
        effective_settings = self._settings_for_recipe(recipe, recipe_base)
        route_override = self._route_override_for_recipe(recipe)
        self.settings = effective_settings
        self.engine = Engine(
            effective_settings,
            route_override=route_override,
        )

        cases = self.case_factory.build(dataset_id, tier, limit=limit)
        if not cases:
            raise ValueError(f"dataset {dataset_id} has no cases for tier {tier.value}")

        entries: dict[str, CandidateManifestEntry] = {}
        for case in cases:
            sources = [Path(path) for path in case.source_paths]
            started = time.perf_counter()
            try:
                job = self.engine.create_job(sources, quality_mode)
                self.engine.run_job(job.job_id)
                elapsed_ms = (time.perf_counter() - started) * 1000
                entries[case.pair_id] = self._candidate_entry(
                    job.job_id,
                    elapsed_ms,
                )
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                entries[case.pair_id] = CandidateManifestEntry(
                    result_path=str(
                        self.settings.harness_dir
                        / "missing"
                        / f"{case.pair_id}.png"
                    ),
                    operational=OperationalMetrics(
                        latency_ms=max(0.0, elapsed_ms),
                        manual_review=True,
                    ),
                    metadata={
                        "execution_error": f"{type(exc).__name__}: {exc}",
                    },
                )

        provider_recipe_id = ""
        if self.engine.provider.available or self.settings.provider_recipe_path is not None:
            try:
                provider_recipe_id = self.engine.provider.recipe().recipe_id
            except Exception:
                provider_recipe_id = ""

        provenance = RunProvenance(
            engine_version=__version__,
            execution_kind="production_engine",
            quality_mode=quality_mode,
            qc_policy_id=self.engine.qc_policy.policy_id,
            qc_policy_version=self.engine.qc_policy.version,
            router_policy_id=self.engine.router_policy.policy_id,
            router_policy_version=self.engine.router_policy.version,
            route_override=route_override,
            provider_recipe_id=provider_recipe_id,
        )
        manifest = CandidateManifest(candidates=entries)
        return HarnessRunner(self.registry, self.store).run_candidates(
            dataset_id,
            tier,
            recipe,
            manifest,
            limit=limit,
            provenance=provenance,
        )
