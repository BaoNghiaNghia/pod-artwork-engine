from __future__ import annotations

import hashlib
from pathlib import Path

from .harness import HarnessStore
from .harness_models import (
    BenchmarkTier,
    HarnessRunStatus,
    MaterialSeparationBenchmarkRecommendation,
    MaterialSeparationBenchmarkReport,
    MaterialSeparationBenchmarkSpec,
    MaterialSeparationExperimentReport,
    MaterialSeparationExperimentSpec,
    MaterialSeparationMaterializationReport,
    MaterialSeparationMaterializationSpec,
    MaterialSeparationPolicyProposal,
    MaterialSeparationPolicyRecommendation,
)
from .material_separation_benchmark import (
    MaterialSeparationBenchmarkError,
    MaterialSeparationBenchmarkMatrixRunner,
    MaterialSeparationMaterializer,
)
from .settings import Settings


class MaterialSeparationExperimentError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_material_separation_policy_proposal(
    spec: MaterialSeparationExperimentSpec,
    *,
    dataset_manifest_sha256: str,
    cohort: MaterialSeparationMaterializationReport | None = None,
    matrix: MaterialSeparationBenchmarkReport | None = None,
) -> MaterialSeparationPolicyProposal:
    if spec.tier is not BenchmarkTier.GOLDEN:
        raise MaterialSeparationExperimentError(
            "material separation policy requires Golden Holdout evidence"
        )
    if not dataset_manifest_sha256:
        raise MaterialSeparationExperimentError(
            "material separation policy requires dataset fingerprint"
        )

    structural: list[str] = []
    safety: list[str] = []
    benefit: list[str] = []

    case_count = cohort.case_count if cohort is not None else 0
    success_count = cohort.success_count if cohort is not None else 0
    failure_count = cohort.failure_count if cohort is not None else 0
    materialization_failure_rate = (
        failure_count / case_count if case_count else 1.0
    )
    existing_alpha_count = cohort.existing_alpha_count if cohort is not None else 0
    simple_border_count = cohort.simple_border_count if cohort is not None else 0
    fail_closed_count = cohort.fail_closed_count if cohort is not None else 0

    if cohort is None:
        structural.append("material_separation_cohort_unavailable")
    else:
        if cohort.dataset_id != spec.dataset_id:
            raise MaterialSeparationExperimentError(
                "material separation cohort dataset mismatch"
            )
        if cohort.tier is not BenchmarkTier.GOLDEN:
            raise MaterialSeparationExperimentError(
                "material separation cohort is not Golden Holdout"
            )
        if cohort.source_run_id != spec.source_run_id:
            raise MaterialSeparationExperimentError(
                "material separation cohort source-run mismatch"
            )
        if cohort.dataset_manifest_sha256 != dataset_manifest_sha256:
            raise MaterialSeparationExperimentError(
                "material separation cohort dataset fingerprint mismatch"
            )
        if cohort.production_execution_enabled or cohort.automatically_applied:
            safety.append("material_separation_cohort_has_unsafe_execution_state")
        if materialization_failure_rate > spec.max_materialization_failure_rate:
            safety.append("material_separation_materialization_failure_rate_exceeds_limit")
        if simple_border_count < spec.min_simple_cases:
            structural.append("simple_border_golden_cases_below_minimum")

    comparable = matrix.comparable_case_count if matrix is not None else 0
    quality_delta = matrix.quality_delta if matrix is not None else None
    semantic_delta = matrix.semantic_delta if matrix is not None else None
    technical_delta = matrix.technical_delta if matrix is not None else None
    alpha_delta = matrix.alpha_delta if matrix is not None else None
    halo_delta = matrix.halo_delta if matrix is not None else None
    detail_delta = matrix.small_detail_delta if matrix is not None else None
    failure_delta = matrix.failure_rate_delta if matrix is not None else 0.0
    manual_delta = (
        matrix.manual_review_rate_delta if matrix is not None else 0.0
    )

    if matrix is None:
        structural.append("material_separation_matrix_unavailable")
    else:
        if matrix.dataset_id != spec.dataset_id:
            raise MaterialSeparationExperimentError(
                "material separation matrix dataset mismatch"
            )
        if matrix.tier is not BenchmarkTier.GOLDEN:
            raise MaterialSeparationExperimentError(
                "material separation matrix is not Golden Holdout"
            )
        if matrix.dataset_manifest_sha256 != dataset_manifest_sha256:
            raise MaterialSeparationExperimentError(
                "material separation matrix dataset fingerprint mismatch"
            )
        if matrix.production_execution_enabled or matrix.automatically_applied:
            safety.append("material_separation_matrix_has_unsafe_execution_state")
        if comparable < spec.min_comparable_cases:
            structural.append("material_separation_comparable_cases_below_minimum")
        if not matrix.sufficient_evidence:
            structural.append("material_separation_matrix_evidence_incomplete")
        if (
            matrix.recommendation
            is MaterialSeparationBenchmarkRecommendation.INSUFFICIENT_EVIDENCE
        ):
            structural.append("material_separation_matrix_reports_insufficient_evidence")
        if (
            matrix.recommendation
            is MaterialSeparationBenchmarkRecommendation.MANUAL_REVIEW
        ):
            safety.append("material_separation_matrix_reports_safety_regression")
        if failure_delta > spec.max_failure_rate_increase:
            safety.append("material_separation_failure_rate_regression")
        if manual_delta > spec.max_manual_review_rate_increase:
            safety.append("material_separation_manual_review_rate_regression")
        if semantic_delta is None:
            structural.append("material_separation_semantic_delta_unavailable")
        elif semantic_delta < -spec.max_semantic_drop:
            safety.append("material_separation_semantic_regression")
        if technical_delta is None:
            structural.append("material_separation_technical_delta_unavailable")
        elif technical_delta < spec.min_technical_gain:
            safety.append("material_separation_technical_regression")
        if detail_delta is None:
            structural.append("material_separation_small_detail_delta_unavailable")
        elif detail_delta < -spec.max_small_detail_drop:
            safety.append("material_separation_small_detail_regression")
        if halo_delta is None:
            structural.append("material_separation_halo_delta_unavailable")
        elif halo_delta < -spec.max_halo_drop:
            safety.append("material_separation_halo_regression")
        if quality_delta is None:
            structural.append("material_separation_quality_delta_unavailable")
        if alpha_delta is None:
            structural.append("material_separation_alpha_delta_unavailable")
        measured_benefit = (
            quality_delta is not None
            and quality_delta >= spec.min_quality_gain
        ) or (
            alpha_delta is not None
            and alpha_delta >= spec.min_alpha_gain
        )
        if not measured_benefit:
            benefit.append("material_separation_gain_below_floor")
        if (
            matrix.recommendation
            is MaterialSeparationBenchmarkRecommendation.KEEP_NATIVE
        ):
            benefit.append("material_separation_matrix_prefers_native")

    structural = list(dict.fromkeys(structural))
    safety = list(dict.fromkeys(safety))
    benefit = list(dict.fromkeys(benefit))

    if safety:
        recommendation = MaterialSeparationPolicyRecommendation.MANUAL_REVIEW
        sufficient = False
    elif structural:
        recommendation = MaterialSeparationPolicyRecommendation.INSUFFICIENT_EVIDENCE
        sufficient = False
    elif benefit:
        recommendation = MaterialSeparationPolicyRecommendation.KEEP_DISABLED
        sufficient = True
    else:
        recommendation = (
            MaterialSeparationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
        )
        sufficient = True

    reasons = [
        "evidence-only material separation policy proposal",
        "production material separation execution remains disabled",
        *structural,
        *safety,
        *benefit,
    ]
    if (
        recommendation
        is MaterialSeparationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
    ):
        reasons.append(
            "Golden material separation evidence passed conservative promotion gates"
        )
    elif recommendation is MaterialSeparationPolicyRecommendation.KEEP_DISABLED:
        reasons.append(
            "Golden evidence is sufficient to keep material separation disabled"
        )

    return MaterialSeparationPolicyProposal(
        experiment_id=spec.experiment_id,
        dataset_id=spec.dataset_id,
        tier=BenchmarkTier.GOLDEN,
        dataset_manifest_sha256=dataset_manifest_sha256,
        source_run_id=spec.source_run_id,
        cohort_id=cohort.cohort_id if cohort is not None else None,
        matrix_id=matrix.matrix_id if matrix is not None else None,
        materialization_case_count=case_count,
        materialization_success_count=success_count,
        materialization_failure_count=failure_count,
        materialization_failure_rate=round(materialization_failure_rate, 8),
        existing_alpha_count=existing_alpha_count,
        simple_border_count=simple_border_count,
        fail_closed_count=fail_closed_count,
        comparable_case_count=comparable,
        quality_delta=quality_delta,
        semantic_delta=semantic_delta,
        technical_delta=technical_delta,
        alpha_delta=alpha_delta,
        halo_delta=halo_delta,
        small_detail_delta=detail_delta,
        failure_rate_delta=failure_delta,
        manual_review_rate_delta=manual_delta,
        min_simple_cases=spec.min_simple_cases,
        min_comparable_cases=spec.min_comparable_cases,
        min_quality_gain=spec.min_quality_gain,
        min_alpha_gain=spec.min_alpha_gain,
        max_semantic_drop=spec.max_semantic_drop,
        max_materialization_failure_rate=spec.max_materialization_failure_rate,
        recommendation=recommendation,
        sufficient_evidence=sufficient,
        requires_human_approval=True,
        automatically_applied=False,
        production_execution_enabled=False,
        reasons=list(dict.fromkeys(reasons)),
    )


class MaterialSeparationExperimentRunner:
    """Golden-only material separation benchmark and evidence-only policy proposal."""

    def __init__(self, settings: Settings, registry, store: HarnessStore) -> None:
        self.settings = settings
        self.registry = registry
        self.store = store

    @staticmethod
    def _resolve(path: str, base: Path | None) -> Path:
        resolved = Path(path).expanduser()
        if not resolved.is_absolute() and base is not None:
            resolved = base / resolved
        return resolved.resolve()

    def run(
        self,
        spec: MaterialSeparationExperimentSpec,
        *,
        spec_base: Path | None = None,
    ) -> MaterialSeparationExperimentReport:
        if spec.tier is not BenchmarkTier.GOLDEN:
            raise MaterialSeparationExperimentError(
                "material separation experiment requires Golden Holdout tier"
            )
        recipe_path = self._resolve(spec.recipe_path, spec_base)
        if not recipe_path.is_file():
            raise FileNotFoundError(f"benchmark recipe not found: {recipe_path}")

        scorecard = self.store.get_scorecard(spec.source_run_id)
        if scorecard is None:
            raise KeyError(f"scorecard not found: {spec.source_run_id}")
        if scorecard.tier is not BenchmarkTier.GOLDEN:
            raise MaterialSeparationExperimentError(
                "material separation source run must be Golden Holdout"
            )
        if scorecard.status is not HarnessRunStatus.COMPLETE:
            raise MaterialSeparationExperimentError(
                "material separation source run must be complete"
            )
        if scorecard.dataset_id != spec.dataset_id:
            raise MaterialSeparationExperimentError(
                "material separation source run dataset mismatch"
            )

        dataset = self.registry.get_dataset(spec.dataset_id)
        if dataset is None:
            raise KeyError(f"dataset not found: {spec.dataset_id}")
        dataset_fingerprint = _sha256(Path(dataset.manifest_path))
        if (
            not scorecard.provenance.dataset_manifest_sha256
            or scorecard.provenance.dataset_manifest_sha256
            != dataset_fingerprint
        ):
            raise MaterialSeparationExperimentError(
                "material separation source-run dataset fingerprint mismatch"
            )

        experiment_dir = self.store.material_separation_experiment_dir(
            spec.experiment_id
        )
        self.store.save_model(experiment_dir / "spec.json", spec)

        cohort: MaterialSeparationMaterializationReport | None = None
        matrix: MaterialSeparationBenchmarkReport | None = None
        try:
            cohort = MaterialSeparationMaterializer(
                self.settings,
                self.registry,
                self.store,
            ).materialize(
                MaterialSeparationMaterializationSpec(
                    cohort_id=f"{spec.experiment_id}_cohort",
                    dataset_id=spec.dataset_id,
                    source_run_id=spec.source_run_id,
                    tier=BenchmarkTier.GOLDEN,
                    min_evidence_confidence=spec.min_evidence_confidence,
                    color_distance_threshold=spec.color_distance_threshold,
                    color_distance_softness=spec.color_distance_softness,
                    limit=spec.limit,
                )
            )
            matrix = MaterialSeparationBenchmarkMatrixRunner(
                self.settings,
                self.registry,
                self.store,
            ).run(
                MaterialSeparationBenchmarkSpec(
                    matrix_id=f"{spec.experiment_id}_matrix",
                    dataset_id=spec.dataset_id,
                    tier=BenchmarkTier.GOLDEN,
                    recipe_path=str(recipe_path),
                    native_manifest_path=cohort.native_manifest_path,
                    separated_manifest_path=cohort.separated_manifest_path,
                    min_comparable_cases=spec.min_comparable_cases,
                    min_quality_gain=spec.min_quality_gain,
                    min_alpha_gain=spec.min_alpha_gain,
                    min_technical_gain=spec.min_technical_gain,
                    max_semantic_drop=spec.max_semantic_drop,
                    max_small_detail_drop=spec.max_small_detail_drop,
                    max_halo_drop=spec.max_halo_drop,
                    max_failure_rate_increase=spec.max_failure_rate_increase,
                    max_manual_review_rate_increase=(
                        spec.max_manual_review_rate_increase
                    ),
                    limit=spec.limit,
                ),
                spec_base=spec_base,
            )
        except MaterialSeparationBenchmarkError:
            raise

        policy = build_material_separation_policy_proposal(
            spec,
            dataset_manifest_sha256=dataset_fingerprint,
            cohort=cohort,
            matrix=matrix,
        )
        cohort_report_path = (
            self.store.material_separation_cohort_dir(cohort.cohort_id)
            / "report.json"
            if cohort is not None
            else None
        )
        matrix_report_path = (
            self.store.material_separation_matrix_dir(matrix.matrix_id)
            / "report.json"
            if matrix is not None
            else None
        )
        policy_path = experiment_dir / "policy-proposal.json"

        report = MaterialSeparationExperimentReport(
            experiment_id=spec.experiment_id,
            dataset_id=spec.dataset_id,
            tier=BenchmarkTier.GOLDEN,
            source_run_id=spec.source_run_id,
            cohort_id=cohort.cohort_id if cohort is not None else None,
            cohort_report_path=(
                str(cohort_report_path)
                if cohort_report_path is not None
                else None
            ),
            matrix_id=matrix.matrix_id if matrix is not None else None,
            matrix_report_path=(
                str(matrix_report_path)
                if matrix_report_path is not None
                else None
            ),
            policy_proposal_path=str(policy_path),
            policy=policy,
            reasons=[
                "Golden material separation experiment is benchmark-only",
                "policy proposal requires explicit human approval",
                "production material separation execution remains disabled",
            ],
        )
        self.store.save_model(policy_path, policy)
        self.store.save_model(experiment_dir / "report.json", report)
        return report
