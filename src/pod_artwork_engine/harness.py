from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from . import __version__
from .contracts import DatasetSplit
from .dataset_registry import DatasetRegistry
from .harness_metrics import aggregate_metric_scores, evaluate_images, write_visual_diff
from .harness_models import (
    BenchmarkCase,
    BenchmarkCaseResult,
    BenchmarkPlan,
    BenchmarkRecipe,
    BenchmarkScorecard,
    BenchmarkTier,
    CandidateManifest,
    CohortScore,
    HarnessRunStatus,
    PromotionDecision,
    PromotionPolicy,
    RunProvenance,
)


def _atomic_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=path.parent,
        prefix=f".{path.stem}.",
        suffix=".tmp",
    ) as handle:
        handle.write(encoded)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.replace(temp_path, path)
    return path


def _sha256_path(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _recipe_sha256(recipe: BenchmarkRecipe) -> str:
    payload = json.dumps(
        recipe.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _as_string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return []


def _mean(values: Iterable[float | None]) -> float | None:
    materialized = [value for value in values if value is not None]
    return sum(materialized) / len(materialized) if materialized else None


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


class HarnessCaseFactory:
    def __init__(self, registry: DatasetRegistry) -> None:
        self.registry = registry

    def build(
        self,
        dataset_id: str,
        tier: BenchmarkTier,
        *,
        limit: int | None = None,
    ) -> list[BenchmarkCase]:
        dataset = self.registry.get_dataset(dataset_id)
        if dataset is None:
            raise KeyError(f"dataset not found: {dataset_id}")

        split = {
            BenchmarkTier.SMOKE: DatasetSplit.TRAIN,
            BenchmarkTier.REGRESSION: DatasetSplit.VALIDATION,
            BenchmarkTier.GOLDEN: DatasetSplit.GOLDEN_HOLDOUT,
        }[tier]
        members = self.registry.list_members(dataset_id, split=split)
        if tier is BenchmarkTier.SMOKE:
            smoke_limit = 8 if limit is None else max(1, limit)
            members = sorted(
                members,
                key=lambda member: hashlib.sha256(
                    f"{dataset_id}:{member.pair_id}".encode("utf-8")
                ).hexdigest(),
            )[:smoke_limit]
        elif limit is not None:
            members = members[: max(1, limit)]

        cases: list[BenchmarkCase] = []
        for member in members:
            pair = self.registry.get_pair(member.pair_id)
            if pair is None:
                continue
            target = self.registry.get_asset(pair.target_asset_id)
            sources = [
                self.registry.get_asset(asset_id)
                for asset_id in pair.source_asset_ids
            ]
            if target is None or any(source is None for source in sources):
                continue

            metadata = dict(pair.metadata)
            cohorts = _as_string_list(metadata.get("cohorts"))
            artwork_type = metadata.get("artwork_type")
            if isinstance(artwork_type, str) and artwork_type not in cohorts:
                cohorts.append(artwork_type)
            difficulty = str(metadata.get("difficulty", "unknown"))
            if difficulty != "unknown":
                difficulty_cohort = f"difficulty:{difficulty}"
                if difficulty_cohort not in cohorts:
                    cohorts.append(difficulty_cohort)
            if not cohorts:
                cohorts = ["uncategorized"]

            exact_text = _as_string_list(metadata.get("exact_text"))
            constraints_value = metadata.get("constraints")
            constraints = constraints_value if isinstance(constraints_value, dict) else {}

            cases.append(
                BenchmarkCase(
                    case_id=f"{tier.value}_{pair.pair_id}",
                    dataset_id=dataset_id,
                    pair_id=pair.pair_id,
                    artwork_identity=pair.artwork_identity,
                    source_paths=[
                        source.path for source in sources if source is not None
                    ],
                    target_path=target.path,
                    exact_text=exact_text,
                    constraints=constraints,
                    difficulty=difficulty,
                    cohorts=sorted(set(cohorts)),
                    metadata=metadata,
                )
            )
        return cases


class HarnessStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.runs_dir = root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def scorecard_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "scorecard.json"

    def save_model(self, path: Path, model: object) -> Path:
        if hasattr(model, "model_dump"):
            payload = model.model_dump(mode="json")
        else:
            payload = model
        return _atomic_json(path, payload)

    def get_scorecard(self, run_id: str) -> BenchmarkScorecard | None:
        path = self.scorecard_path(run_id)
        if not path.is_file():
            return None
        return BenchmarkScorecard.model_validate_json(path.read_text(encoding="utf-8"))

    def list_scorecards(self) -> list[BenchmarkScorecard]:
        scorecards: list[BenchmarkScorecard] = []
        for path in self.runs_dir.glob("*/scorecard.json"):
            try:
                scorecards.append(
                    BenchmarkScorecard.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError):
                continue
        return sorted(scorecards, key=lambda item: item.created_at, reverse=True)

    def get_results(self, run_id: str) -> list[BenchmarkCaseResult]:
        results_dir = self.run_dir(run_id) / "results"
        if not results_dir.is_dir():
            return []
        results: list[BenchmarkCaseResult] = []
        for path in sorted(results_dir.glob("*.json")):
            try:
                results.append(
                    BenchmarkCaseResult.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                )
            except (OSError, ValueError):
                continue
        return results

    def suite_dir(self, suite_id: str) -> Path:
        return self.root / "suites" / suite_id

    def calibration_dir(self, proposal_id: str) -> Path:
        return self.root / "calibration" / proposal_id

    def route_matrix_dir(self, matrix_id: str) -> Path:
        return self.root / "route-matrices" / matrix_id

    def router_calibration_dir(self, proposal_id: str) -> Path:
        return self.root / "router-calibration" / proposal_id


class HarnessRunner:
    def __init__(self, registry: DatasetRegistry, store: HarnessStore) -> None:
        self.registry = registry
        self.store = store
        self.case_factory = HarnessCaseFactory(registry)

    def run_candidates(
        self,
        dataset_id: str,
        tier: BenchmarkTier,
        recipe: BenchmarkRecipe,
        manifest: CandidateManifest,
        *,
        manifest_base: Path | None = None,
        limit: int | None = None,
        provenance: RunProvenance | None = None,
    ) -> BenchmarkScorecard:
        cases = self.case_factory.build(dataset_id, tier, limit=limit)
        if not cases:
            raise ValueError(f"dataset {dataset_id} has no cases for tier {tier.value}")

        dataset = self.registry.get_dataset(dataset_id)
        if dataset is None:
            raise KeyError(f"dataset not found: {dataset_id}")
        dataset_manifest_sha256 = _sha256_path(Path(dataset.manifest_path))
        recipe_sha256 = _recipe_sha256(recipe)
        provenance = provenance or RunProvenance()
        provenance = provenance.model_copy(
            update={
                "engine_version": provenance.engine_version or __version__,
                "recipe_sha256": provenance.recipe_sha256 or recipe_sha256,
                "dataset_manifest_sha256": (
                    provenance.dataset_manifest_sha256
                    or dataset_manifest_sha256
                ),
            }
        )

        run_id = "run_" + uuid4().hex
        run_dir = self.store.run_dir(run_id)
        results_dir = run_dir / "results"
        diffs_dir = run_dir / "diffs"
        results_dir.mkdir(parents=True, exist_ok=True)
        diffs_dir.mkdir(parents=True, exist_ok=True)

        self.store.save_model(run_dir / "recipe.json", recipe)
        plan = BenchmarkPlan(
            run_id=run_id,
            dataset_id=dataset_id,
            tier=tier,
            recipe_id=recipe.recipe_id,
            recipe_version=recipe.version,
            case_ids=[case.case_id for case in cases],
            provenance=provenance,
        )
        self.store.save_model(run_dir / "plan.json", plan)
        _atomic_json(
            run_dir / "cases.json",
            [case.model_dump(mode="json") for case in cases],
        )

        results: list[BenchmarkCaseResult] = []
        for case in cases:
            entry = (
                manifest.candidates.get(case.pair_id)
                or manifest.candidates.get(case.case_id)
                or manifest.candidates.get(case.artwork_identity)
            )
            if entry is None:
                result = BenchmarkCaseResult(
                    case_id=case.case_id,
                    pair_id=case.pair_id,
                    artwork_identity=case.artwork_identity,
                    success=False,
                    error="candidate missing from manifest",
                    cohorts=case.cohorts,
                )
            else:
                result_path = Path(entry.result_path).expanduser()
                if not result_path.is_absolute() and manifest_base is not None:
                    result_path = manifest_base / result_path
                result_path = result_path.resolve()

                if not result_path.is_file():
                    result = BenchmarkCaseResult(
                        case_id=case.case_id,
                        pair_id=case.pair_id,
                        artwork_identity=case.artwork_identity,
                        candidate_path=str(result_path),
                        success=False,
                        error="candidate file not found",
                        operational=entry.operational,
                        precision=entry.precision,
                        route=entry.route,
                        runtime_qc=entry.runtime_qc,
                        cohorts=case.cohorts,
                    )
                else:
                    try:
                        semantic, technical = evaluate_images(
                            Path(case.target_path),
                            result_path,
                            expected_text=case.exact_text,
                            recognized_text=entry.recognized_text,
                        )
                        if (
                            entry.semantic_judge is not None
                            and entry.semantic_judge.object_fidelity is not None
                        ):
                            semantic.object_fidelity = (
                                entry.semantic_judge.object_fidelity
                            )
                        semantic_score, technical_score, quality_score = aggregate_metric_scores(
                            semantic,
                            technical,
                        )
                        diff_path = write_visual_diff(
                            Path(case.target_path),
                            result_path,
                            diffs_dir / f"{case.case_id}.png",
                        )
                        result = BenchmarkCaseResult(
                            case_id=case.case_id,
                            pair_id=case.pair_id,
                            artwork_identity=case.artwork_identity,
                            candidate_path=str(result_path),
                            success=True,
                            semantic=semantic,
                            technical=technical,
                            operational=entry.operational,
                            precision=entry.precision,
                            route=entry.route,
                            runtime_qc=entry.runtime_qc,
                            semantic_score=semantic_score,
                            technical_score=technical_score,
                            quality_score=quality_score,
                            diff_path=str(diff_path),
                            cohorts=case.cohorts,
                        )
                    except Exception as exc:
                        result = BenchmarkCaseResult(
                            case_id=case.case_id,
                            pair_id=case.pair_id,
                            artwork_identity=case.artwork_identity,
                            candidate_path=str(result_path),
                            success=False,
                            error=f"{type(exc).__name__}: {exc}",
                            operational=entry.operational,
                            precision=entry.precision,
                            route=entry.route,
                            runtime_qc=entry.runtime_qc,
                            cohorts=case.cohorts,
                        )

            result_file = results_dir / f"{case.case_id}.json"
            self.store.save_model(result_file, result)
            results.append(result)

        scorecard = build_scorecard(
            run_id=run_id,
            dataset_id=dataset_id,
            tier=tier,
            recipe=recipe,
            results=results,
            provenance=provenance,
        )
        self.store.save_model(run_dir / "scorecard.json", scorecard)
        return scorecard


def _cohort_score(cohort: str, results: list[BenchmarkCaseResult]) -> CohortScore:
    successes = [result for result in results if result.success]
    return CohortScore(
        cohort=cohort,
        case_count=len(results),
        success_count=len(successes),
        quality_mean=_mean(result.quality_score for result in successes),
        semantic_mean=_mean(result.semantic_score for result in successes),
        technical_mean=_mean(result.technical_score for result in successes),
        failure_rate=(len(results) - len(successes)) / max(1, len(results)),
    )


def _metric_coverage(results: list[BenchmarkCaseResult]) -> dict[str, float]:
    successes = [result for result in results if result.success]
    if not successes:
        return {}

    fields: dict[str, list[float | None]] = {
        "semantic.exact_text": [result.semantic.exact_text for result in successes],
        "semantic.layout": [result.semantic.layout for result in successes],
        "semantic.object_fidelity": [
            result.semantic.object_fidelity for result in successes
        ],
        "semantic.color": [result.semantic.color for result in successes],
        "semantic.texture": [result.semantic.texture for result in successes],
        "semantic.missing_detail": [
            result.semantic.missing_detail for result in successes
        ],
        "technical.edge": [result.technical.edge for result in successes],
        "technical.alpha": [result.technical.alpha for result in successes],
        "technical.halo_aliasing": [
            result.technical.halo_aliasing for result in successes
        ],
        "technical.blur": [result.technical.blur for result in successes],
        "technical.effective_resolution": [
            result.technical.effective_resolution for result in successes
        ],
        "technical.small_detail_survival": [
            result.technical.small_detail_survival for result in successes
        ],
    }
    return {
        name: sum(value is not None for value in values) / len(successes)
        for name, values in fields.items()
    }


def _precision_coverage(results: list[BenchmarkCaseResult]) -> dict[str, float]:
    successes = [result for result in results if result.success]
    if not successes:
        return {}

    predicates = {
        "multi_reference_fusion": lambda result: result.precision.multi_reference_fusion,
        "region_confidence_map": lambda result: result.precision.region_confidence_map,
        "local_ocr": lambda result: result.precision.local_ocr,
        "visual_font_match": lambda result: result.precision.visual_font_match,
        "typography_rebuilt": lambda result: result.precision.typography_rebuilt,
        "mixed_text_refined": lambda result: result.precision.mixed_text_refined,
        "local_text_repair": lambda result: result.precision.local_text_repair,
        "geometry_vector": lambda result: result.precision.geometry_vector,
        "compound_geometry": lambda result: result.precision.compound_geometry,
        "masked_text_regions": lambda result: result.precision.masked_text_regions > 0,
        "provider_recipe": lambda result: bool(result.precision.provider_recipe_id),
    }
    return {
        name: sum(bool(predicate(result)) for result in successes) / len(successes)
        for name, predicate in predicates.items()
    }


def build_scorecard(
    *,
    run_id: str,
    dataset_id: str,
    tier: BenchmarkTier,
    recipe: BenchmarkRecipe,
    results: list[BenchmarkCaseResult],
    provenance: RunProvenance | None = None,
) -> BenchmarkScorecard:
    successes = [result for result in results if result.success]
    failures = [result for result in results if not result.success]
    manual_reviews = [
        result for result in results if result.operational.manual_review
    ]
    latencies = [
        result.operational.latency_ms
        for result in results
        if result.operational.latency_ms > 0
    ]

    cohorts: dict[str, list[BenchmarkCaseResult]] = defaultdict(list)
    for result in results:
        labels = result.cohorts or ["uncategorized"]
        for cohort in labels:
            cohorts[cohort].append(result)

    if not successes:
        status = HarnessRunStatus.FAILED
    elif failures:
        status = HarnessRunStatus.PARTIAL
    else:
        status = HarnessRunStatus.COMPLETE

    return BenchmarkScorecard(
        run_id=run_id,
        dataset_id=dataset_id,
        tier=tier,
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.version,
        status=status,
        case_count=len(results),
        success_count=len(successes),
        failure_count=len(failures),
        manual_review_count=len(manual_reviews),
        quality_mean=_mean(result.quality_score for result in successes),
        semantic_mean=_mean(result.semantic_score for result in successes),
        technical_mean=_mean(result.technical_score for result in successes),
        failure_rate=len(failures) / max(1, len(results)),
        manual_review_rate=len(manual_reviews) / max(1, len(results)),
        latency_p50_ms=_percentile(latencies, 0.50),
        latency_p95_ms=_percentile(latencies, 0.95),
        provider_calls=sum(result.operational.provider_calls for result in results),
        retries=sum(result.operational.retries for result in results),
        total_cost_usd=sum(result.operational.cost_usd for result in results),
        metric_coverage=_metric_coverage(results),
        precision_coverage=_precision_coverage(results),
        provenance=provenance or RunProvenance(),
        cohorts={
            cohort: _cohort_score(cohort, cohort_results)
            for cohort, cohort_results in sorted(cohorts.items())
        },
        result_paths=[
            result.diff_path for result in results if result.diff_path is not None
        ],
    )


def compare_scorecards(
    champion: BenchmarkScorecard,
    challenger: BenchmarkScorecard,
    policy: PromotionPolicy | None = None,
) -> PromotionDecision:
    policy = policy or PromotionPolicy()
    reasons: list[str] = []
    cohort_deltas: dict[str, float] = {}

    if champion.dataset_id != challenger.dataset_id:
        reasons.append("dataset mismatch")
    if champion.tier != challenger.tier:
        reasons.append("benchmark tier mismatch")
    if champion.case_count != challenger.case_count:
        reasons.append("case count mismatch")
    if (
        champion.provenance.dataset_manifest_sha256
        and challenger.provenance.dataset_manifest_sha256
        and champion.provenance.dataset_manifest_sha256
        != challenger.provenance.dataset_manifest_sha256
    ):
        reasons.append("dataset manifest fingerprint mismatch")
    if (
        champion.provenance.quality_mode is not None
        and challenger.provenance.quality_mode is not None
        and champion.provenance.quality_mode
        is not challenger.provenance.quality_mode
    ):
        reasons.append("quality mode mismatch")
    if policy.require_complete and (
        champion.status is not HarnessRunStatus.COMPLETE
        or challenger.status is not HarnessRunStatus.COMPLETE
    ):
        reasons.append("promotion comparison requires complete scorecards")
    if policy.require_golden and (
        champion.tier is not BenchmarkTier.GOLDEN
        or challenger.tier is not BenchmarkTier.GOLDEN
    ):
        reasons.append("promotion requires Golden Holdout scorecards")

    overall_delta: float | None = None
    if champion.quality_mean is None or challenger.quality_mean is None:
        reasons.append("quality score unavailable")
    else:
        overall_delta = challenger.quality_mean - champion.quality_mean
        if overall_delta < policy.min_overall_delta:
            reasons.append(
                f"overall quality delta {overall_delta:.4f} below required "
                f"{policy.min_overall_delta:.4f}"
            )

    for cohort in sorted(set(champion.cohorts) & set(challenger.cohorts)):
        old = champion.cohorts[cohort].quality_mean
        new = challenger.cohorts[cohort].quality_mean
        if old is None or new is None:
            continue
        delta = new - old
        cohort_deltas[cohort] = delta
        if delta < -policy.max_cohort_drop:
            reasons.append(
                f"cohort {cohort} regressed by {abs(delta):.4f} "
                f"(limit {policy.max_cohort_drop:.4f})"
            )

    failure_increase = challenger.failure_rate - champion.failure_rate
    if failure_increase > policy.max_failure_rate_increase:
        reasons.append(
            f"failure rate increased by {failure_increase:.4f}"
        )

    review_increase = challenger.manual_review_rate - champion.manual_review_rate
    if review_increase > policy.max_manual_review_rate_increase:
        reasons.append(
            f"manual review rate increased by {review_increase:.4f}"
        )

    if policy.max_latency_ratio is not None and champion.latency_p95_ms > 0:
        latency_ratio = challenger.latency_p95_ms / champion.latency_p95_ms
        if latency_ratio > policy.max_latency_ratio:
            reasons.append(
                f"p95 latency ratio {latency_ratio:.3f} exceeds "
                f"{policy.max_latency_ratio:.3f}"
            )

    champion_cost_per_case = champion.total_cost_usd / max(1, champion.case_count)
    challenger_cost_per_case = challenger.total_cost_usd / max(1, challenger.case_count)
    if policy.max_cost_ratio is not None and champion_cost_per_case > 0:
        cost_ratio = challenger_cost_per_case / champion_cost_per_case
        if cost_ratio > policy.max_cost_ratio:
            reasons.append(
                f"cost ratio {cost_ratio:.3f} exceeds {policy.max_cost_ratio:.3f}"
            )

    return PromotionDecision(
        champion_scorecard_id=champion.scorecard_id,
        challenger_scorecard_id=challenger.scorecard_id,
        eligible=not reasons,
        reasons=reasons,
        overall_delta=overall_delta,
        cohort_deltas=cohort_deltas,
    )


def load_recipe(path: Path) -> BenchmarkRecipe:
    return BenchmarkRecipe.model_validate_json(path.read_text(encoding="utf-8"))


def load_candidate_manifest(path: Path) -> CandidateManifest:
    return CandidateManifest.model_validate_json(path.read_text(encoding="utf-8"))
