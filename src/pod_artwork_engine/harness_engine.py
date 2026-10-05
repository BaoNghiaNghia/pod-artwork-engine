from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from .contracts import JobState, ProviderResult, QualityMode, RegionReplacementMode
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

        local_ocr = metadata.get("local_ocr_enabled")
        if isinstance(local_ocr, bool):
            overrides["local_ocr_enabled"] = local_ocr

        ocr_language = metadata.get("tesseract_language")
        if isinstance(ocr_language, str) and ocr_language.strip():
            overrides["tesseract_language"] = ocr_language.strip()

        return replace(self.base_settings, **overrides)

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

        return PrecisionEvidence(
            local_ocr=isinstance(local_ocr, dict) and bool(local_ocr.get("exact_text")),
            ocr_backend=(
                str(local_ocr.get("backend") or "")
                if isinstance(local_ocr, dict)
                else ""
            ),
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
            masked_text_regions=masked_regions,
            provider_recipe_id=recipe_id,
            precision_ops=sorted(set(precision_ops)),
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
        self.settings = effective_settings
        self.engine = Engine(effective_settings)

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

        manifest = CandidateManifest(candidates=entries)
        return HarnessRunner(self.registry, self.store).run_candidates(
            dataset_id,
            tier,
            recipe,
            manifest,
            limit=limit,
        )
