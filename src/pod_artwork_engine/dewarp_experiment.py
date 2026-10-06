from __future__ import annotations

from pathlib import Path

from .dewarp_benchmark import DewarpBenchmarkMatrixRunner, DewarpMaterializer
from .harness import HarnessStore
from .harness_models import (
    BenchmarkTier,
    DewarpBenchmarkRecommendation,
    DewarpBenchmarkReport,
    DewarpBenchmarkSpec,
    DewarpExperimentReport,
    DewarpExperimentSpec,
    DewarpMaterializationReport,
    DewarpMaterializationSpec,
    HarnessRunStatus,
    ProductionRegistrationPolicyProposal,
    ProductionRegistrationPolicyRecommendation,
    RegistrationPolicyProposal,
    RegistrationPolicyRecommendation,
)
from .registration_calibration import RegistrationPolicyCalibrator
from .settings import Settings


class DewarpExperimentError(RuntimeError):
    pass


def build_production_registration_policy_proposal(
    spec: DewarpExperimentSpec,
    calibration: RegistrationPolicyProposal,
    *,
    cohort: DewarpMaterializationReport | None = None,
    matrix: DewarpBenchmarkReport | None = None,
    materialization_source_run_id: str | None = None,
) -> ProductionRegistrationPolicyProposal:
    if spec.tier is not BenchmarkTier.GOLDEN:
        raise DewarpExperimentError(
            "production registration policy requires Golden Holdout evidence"
        )
    if calibration.dataset_id != spec.dataset_id:
        raise DewarpExperimentError(
            "registration calibration dataset does not match experiment"
        )
    if set(calibration.source_run_ids) != set(spec.registration_run_ids):
        raise DewarpExperimentError(
            "registration calibration source-run provenance mismatch"
        )
    if any(tier is not BenchmarkTier.GOLDEN for tier in calibration.source_tiers):
        raise DewarpExperimentError(
            "registration calibration contains non-Golden provenance"
        )

    structural: list[str] = []
    safety: list[str] = []
    benefit: list[str] = []

    if not calibration.dataset_manifest_sha256:
        structural.append("dataset_fingerprint_unavailable")
    if not calibration.sufficient_evidence:
        structural.append("registration_calibration_insufficient")
    if calibration.recommendation in {
        RegistrationPolicyRecommendation.INSUFFICIENT_EVIDENCE,
        RegistrationPolicyRecommendation.MANUAL_REVIEW,
    }:
        structural.append("registration_calibration_not_benchmark_ready")
    if (
        calibration.recommendation
        is RegistrationPolicyRecommendation.MIXED_FOR_DEWARP_BENCHMARK
        and not (
            calibration.affine.sufficient_evidence
            and calibration.homography.sufficient_evidence
        )
    ):
        structural.append("mixed_registration_policy_is_not_fully_supported")
    if calibration.automatically_applied or calibration.production_execution_enabled:
        safety.append("registration_calibration_has_unsafe_execution_state")
    if not calibration.requires_human_approval:
        safety.append("registration_calibration_missing_human_approval_gate")

    case_count = cohort.case_count if cohort is not None else 0
    success_count = cohort.success_count if cohort is not None else 0
    failure_count = cohort.failure_count if cohort is not None else 0
    failure_rate = (
        failure_count / case_count
        if case_count
        else 1.0
    )
    region_improvement_rate = (
        cohort.region_improved_case_count / success_count
        if cohort is not None and success_count
        else 0.0
    )

    if cohort is None:
        structural.append("dewarp_materialization_unavailable")
    else:
        if cohort.dataset_id != spec.dataset_id or cohort.tier is not BenchmarkTier.GOLDEN:
            raise DewarpExperimentError("dewarp cohort provenance mismatch")
        if cohort.registration_policy_id != calibration.proposal_id:
            raise DewarpExperimentError(
                "dewarp cohort registration policy provenance mismatch"
            )
        if materialization_source_run_id != cohort.source_run_id:
            raise DewarpExperimentError(
                "dewarp cohort source-run provenance mismatch"
            )
        if (
            calibration.dataset_manifest_sha256
            != cohort.dataset_manifest_sha256
        ):
            raise DewarpExperimentError(
                "dewarp cohort dataset fingerprint mismatch"
            )
        if cohort.production_execution_enabled or cohort.automatically_applied:
            safety.append("dewarp_materialization_has_unsafe_execution_state")
        if failure_rate > spec.max_materialization_failure_rate:
            safety.append("dewarp_materialization_failure_rate_exceeds_limit")
        if region_improvement_rate < spec.min_region_improvement_rate:
            benefit.append("registration_region_improvement_below_floor")

    comparable = matrix.comparable_case_count if matrix is not None else 0
    quality_delta = matrix.quality_delta if matrix is not None else None
    technical_delta = matrix.technical_delta if matrix is not None else None
    detail_delta = matrix.small_detail_delta if matrix is not None else None
    failure_delta = matrix.failure_rate_delta if matrix is not None else 0.0
    manual_delta = (
        matrix.manual_review_rate_delta if matrix is not None else 0.0
    )

    if matrix is None:
        structural.append("dewarp_benchmark_matrix_unavailable")
    else:
        if matrix.dataset_id != spec.dataset_id or matrix.tier is not BenchmarkTier.GOLDEN:
            raise DewarpExperimentError("dewarp matrix provenance mismatch")
        if (
            calibration.dataset_manifest_sha256
            != matrix.dataset_manifest_sha256
        ):
            raise DewarpExperimentError(
                "dewarp matrix dataset fingerprint mismatch"
            )
        if matrix.production_execution_enabled or matrix.automatically_applied:
            safety.append("dewarp_matrix_has_unsafe_execution_state")
        if comparable < spec.min_comparable_cases:
            structural.append("comparable_golden_cases_below_minimum")
        if not matrix.sufficient_evidence:
            structural.append("dewarp_matrix_evidence_is_incomplete")
        if matrix.recommendation is DewarpBenchmarkRecommendation.INSUFFICIENT_EVIDENCE:
            structural.append("dewarp_matrix_reports_insufficient_evidence")
        if matrix.recommendation is DewarpBenchmarkRecommendation.MANUAL_REVIEW:
            safety.append("dewarp_matrix_reports_safety_regression")
        if failure_delta > spec.max_failure_rate_increase:
            safety.append("dewarp_failure_rate_regression")
        if manual_delta > spec.max_manual_review_rate_increase:
            safety.append("dewarp_manual_review_rate_regression")
        if technical_delta is None:
            structural.append("dewarp_technical_delta_unavailable")
        elif technical_delta < spec.min_technical_gain:
            safety.append("dewarp_technical_regression")
        if detail_delta is None:
            structural.append("dewarp_small_detail_delta_unavailable")
        elif detail_delta < spec.min_small_detail_delta:
            safety.append("dewarp_small_detail_regression")
        if quality_delta is None:
            structural.append("dewarp_quality_delta_unavailable")
        elif quality_delta < spec.min_quality_gain:
            benefit.append("dewarp_quality_gain_below_floor")
        if matrix.recommendation is DewarpBenchmarkRecommendation.KEEP_NATIVE:
            benefit.append("dewarp_matrix_prefers_native")

    structural = list(dict.fromkeys(structural))
    safety = list(dict.fromkeys(safety))
    benefit = list(dict.fromkeys(benefit))

    if safety:
        recommendation = ProductionRegistrationPolicyRecommendation.MANUAL_REVIEW
        sufficient = False
    elif structural:
        recommendation = (
            ProductionRegistrationPolicyRecommendation.INSUFFICIENT_EVIDENCE
        )
        sufficient = False
    elif benefit:
        recommendation = ProductionRegistrationPolicyRecommendation.KEEP_DISABLED
        sufficient = True
    else:
        recommendation = (
            ProductionRegistrationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
        )
        sufficient = True

    reasons = [
        "evidence-only production registration policy proposal",
        "production dewarp execution remains disabled",
        *structural,
        *safety,
        *benefit,
    ]
    if recommendation is ProductionRegistrationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL:
        reasons.append(
            "Golden dewarp evidence passed conservative production-readiness gates"
        )
    elif recommendation is ProductionRegistrationPolicyRecommendation.KEEP_DISABLED:
        reasons.append(
            "Golden evidence is sufficient to keep production registration disabled"
        )

    return ProductionRegistrationPolicyProposal(
        experiment_id=spec.experiment_id,
        dataset_id=spec.dataset_id,
        tier=BenchmarkTier.GOLDEN,
        dataset_manifest_sha256=calibration.dataset_manifest_sha256,
        calibration_proposal_id=calibration.proposal_id,
        calibration_recommendation=calibration.recommendation,
        source_registration_run_ids=list(spec.registration_run_ids),
        materialization_source_run_id=materialization_source_run_id,
        cohort_id=cohort.cohort_id if cohort is not None else None,
        matrix_id=matrix.matrix_id if matrix is not None else None,
        affine_thresholds=(
            calibration.affine.thresholds
            if calibration.affine.sufficient_evidence
            else None
        ),
        homography_thresholds=(
            calibration.homography.thresholds
            if calibration.homography.sufficient_evidence
            else None
        ),
        calibration_measured_reference_count=(
            calibration.measured_reference_count
        ),
        materialization_case_count=case_count,
        materialization_success_count=success_count,
        materialization_failure_count=failure_count,
        materialization_failure_rate=round(failure_rate, 8),
        affine_count=cohort.affine_count if cohort is not None else 0,
        homography_count=cohort.homography_count if cohort is not None else 0,
        region_improved_case_count=(
            cohort.region_improved_case_count if cohort is not None else 0
        ),
        region_improvement_rate=round(region_improvement_rate, 8),
        comparable_case_count=comparable,
        quality_delta=quality_delta,
        technical_delta=technical_delta,
        small_detail_delta=detail_delta,
        failure_rate_delta=failure_delta,
        manual_review_rate_delta=manual_delta,
        min_comparable_cases=spec.min_comparable_cases,
        min_quality_gain=spec.min_quality_gain,
        min_technical_gain=spec.min_technical_gain,
        min_small_detail_delta=spec.min_small_detail_delta,
        min_region_improvement_rate=spec.min_region_improvement_rate,
        max_materialization_failure_rate=spec.max_materialization_failure_rate,
        max_failure_rate_increase=spec.max_failure_rate_increase,
        max_manual_review_rate_increase=(
            spec.max_manual_review_rate_increase
        ),
        recommendation=recommendation,
        sufficient_evidence=sufficient,
        requires_human_approval=True,
        automatically_applied=False,
        production_execution_enabled=False,
        reasons=list(dict.fromkeys(reasons)),
    )


class DewarpExperimentRunner:
    """Run Golden registration calibration -> dewarp cohort -> matrix -> proposal."""

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

    def _select_source_run(
        self,
        spec: DewarpExperimentSpec,
        calibration: RegistrationPolicyProposal,
    ) -> str:
        if spec.materialization_source_run_id is not None:
            if spec.materialization_source_run_id not in calibration.source_run_ids:
                raise DewarpExperimentError(
                    "materialization source run is outside calibration provenance"
                )
            return spec.materialization_source_run_id

        scorecards = []
        for run_id in calibration.source_run_ids:
            scorecard = self.store.get_scorecard(run_id)
            if scorecard is None:
                continue
            if (
                scorecard.tier is BenchmarkTier.GOLDEN
                and scorecard.status is HarnessRunStatus.COMPLETE
                and scorecard.dataset_id == spec.dataset_id
            ):
                scorecards.append(scorecard)
        if not scorecards:
            raise DewarpExperimentError(
                "no complete Golden source run is available for dewarp materialization"
            )
        scorecards.sort(
            key=lambda item: (
                -item.case_count,
                -item.success_count,
                item.run_id,
            )
        )
        return scorecards[0].run_id

    def run(
        self,
        spec: DewarpExperimentSpec,
        *,
        spec_base: Path | None = None,
    ) -> DewarpExperimentReport:
        if spec.tier is not BenchmarkTier.GOLDEN:
            raise DewarpExperimentError(
                "dewarp experiment requires Golden Holdout tier"
            )

        recipe_path = self._resolve(spec.recipe_path, spec_base)
        if not recipe_path.is_file():
            raise FileNotFoundError(f"benchmark recipe not found: {recipe_path}")

        experiment_dir = self.store.dewarp_experiment_dir(spec.experiment_id)
        self.store.save_model(experiment_dir / "spec.json", spec)

        calibration = RegistrationPolicyCalibrator(self.store).propose(
            spec.registration_run_ids,
            min_cases_per_lane=spec.calibration_min_cases_per_lane,
            min_quality_score=spec.calibration_min_quality_score,
            max_manual_review_rate=spec.calibration_max_manual_review_rate,
        )
        if calibration.dataset_id != spec.dataset_id:
            raise DewarpExperimentError(
                "calibrated registration dataset does not match experiment"
            )
        calibration_path = (
            self.store.registration_calibration_dir(calibration.proposal_id)
            / "proposal.json"
        )

        cohort: DewarpMaterializationReport | None = None
        matrix: DewarpBenchmarkReport | None = None
        source_run_id: str | None = None

        benchmark_ready = (
            calibration.sufficient_evidence
            and calibration.recommendation
            not in {
                RegistrationPolicyRecommendation.INSUFFICIENT_EVIDENCE,
                RegistrationPolicyRecommendation.MANUAL_REVIEW,
            }
        )
        if benchmark_ready:
            source_run_id = self._select_source_run(spec, calibration)
            cohort = DewarpMaterializer(
                self.settings,
                self.registry,
                self.store,
            ).materialize(
                DewarpMaterializationSpec(
                    cohort_id=f"{spec.experiment_id}_cohort",
                    dataset_id=spec.dataset_id,
                    source_run_id=source_run_id,
                    registration_policy_path=str(calibration_path),
                    tier=BenchmarkTier.GOLDEN,
                    canonical_size=spec.canonical_size,
                    limit=spec.limit,
                )
            )
            matrix = DewarpBenchmarkMatrixRunner(
                self.settings,
                self.registry,
                self.store,
            ).run(
                DewarpBenchmarkSpec(
                    matrix_id=f"{spec.experiment_id}_matrix",
                    dataset_id=spec.dataset_id,
                    tier=BenchmarkTier.GOLDEN,
                    recipe_path=str(recipe_path),
                    native_manifest_path=cohort.native_manifest_path,
                    dewarp_manifest_path=cohort.dewarp_manifest_path,
                    min_quality_gain=spec.min_quality_gain,
                    min_technical_gain=spec.min_technical_gain,
                    min_comparable_cases=spec.min_comparable_cases,
                    max_failure_rate_increase=spec.max_failure_rate_increase,
                    max_manual_review_rate_increase=(
                        spec.max_manual_review_rate_increase
                    ),
                    limit=spec.limit,
                ),
                spec_base=spec_base,
            )

        policy = build_production_registration_policy_proposal(
            spec,
            calibration,
            cohort=cohort,
            matrix=matrix,
            materialization_source_run_id=source_run_id,
        )

        cohort_report_path = (
            self.store.dewarp_cohort_dir(cohort.cohort_id) / "report.json"
            if cohort is not None
            else None
        )
        matrix_report_path = (
            self.store.dewarp_matrix_dir(matrix.matrix_id) / "report.json"
            if matrix is not None
            else None
        )
        policy_path = experiment_dir / "production-policy-proposal.json"

        report = DewarpExperimentReport(
            experiment_id=spec.experiment_id,
            dataset_id=spec.dataset_id,
            tier=BenchmarkTier.GOLDEN,
            source_registration_run_ids=list(spec.registration_run_ids),
            materialization_source_run_id=source_run_id,
            calibration_proposal_id=calibration.proposal_id,
            calibration_proposal_path=str(calibration_path),
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
                "Golden dewarp experiment is benchmark-only",
                "production registration proposal requires explicit human approval",
                "production dewarp execution remains disabled",
            ],
        )
        self.store.save_model(policy_path, policy)
        self.store.save_model(experiment_dir / "report.json", report)
        return report
