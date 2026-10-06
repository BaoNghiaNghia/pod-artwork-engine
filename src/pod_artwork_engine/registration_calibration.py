from __future__ import annotations

import math
from collections import defaultdict

from .contracts import (
    FeatureCorrespondenceDisposition,
    MultiReferenceCorrespondenceEvidence,
)
from .feature_correspondence import (
    HOMOGRAPHY_IMPROVEMENT_RATIO,
    MAX_AFFINE_MEAN_ERROR,
    MAX_AFFINE_MEDIAN_ERROR,
    MAX_HOMOGRAPHY_MEAN_ERROR,
    MAX_HOMOGRAPHY_MEDIAN_ERROR,
    MIN_AFFINE_INLIERS,
    MIN_AFFINE_INLIER_RATIO,
    MIN_HOMOGRAPHY_INLIERS,
    MIN_HOMOGRAPHY_INLIER_RATIO,
    MIN_MATCH_COUNT,
    MIN_SPATIAL_COVERAGE,
)
from .harness import HarnessStore
from .harness_models import (
    BenchmarkTier,
    HarnessRunStatus,
    RegistrationLaneCalibration,
    RegistrationPolicyProposal,
    RegistrationPolicyRecommendation,
    RegistrationThresholds,
)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _round(value: float | None, digits: int = 8) -> float | None:
    return round(value, digits) if value is not None else None


def _lane_calibration(
    lane: str,
    samples: list[dict[str, float]],
    case_ids: set[str],
    *,
    min_cases: int,
) -> RegistrationLaneCalibration:
    reasons: list[str] = []
    match_counts = [sample["match_count"] for sample in samples]
    inlier_counts = [sample["inlier_count"] for sample in samples]
    inlier_ratios = [sample["inlier_ratio"] for sample in samples]
    coverages = [sample["spatial_coverage"] for sample in samples]
    mean_errors = [sample["mean_error"] for sample in samples]
    median_errors = [sample["median_error"] for sample in samples]
    homography_ratios = [
        sample["homography_error_ratio"]
        for sample in samples
        if sample.get("homography_error_ratio") is not None
    ]

    inlier_p10 = _percentile(inlier_ratios, 0.10)
    coverage_p10 = _percentile(coverages, 0.10)
    mean_error_p90 = _percentile(mean_errors, 0.90)
    median_error_p90 = _percentile(median_errors, 0.90)
    homography_ratio_p90 = _percentile(homography_ratios, 0.90)

    base_min_inliers = (
        MIN_HOMOGRAPHY_INLIERS if lane == "homography" else MIN_AFFINE_INLIERS
    )
    base_min_ratio = (
        MIN_HOMOGRAPHY_INLIER_RATIO
        if lane == "homography"
        else MIN_AFFINE_INLIER_RATIO
    )
    base_max_mean = (
        MAX_HOMOGRAPHY_MEAN_ERROR if lane == "homography" else MAX_AFFINE_MEAN_ERROR
    )
    base_max_median = (
        MAX_HOMOGRAPHY_MEDIAN_ERROR
        if lane == "homography"
        else MAX_AFFINE_MEDIAN_ERROR
    )

    if len(case_ids) < min_cases:
        reasons.append("insufficient_golden_cases")
    if not samples:
        reasons.append("no_measured_registration_samples")
    if inlier_p10 is not None and inlier_p10 < base_min_ratio:
        reasons.append("golden_inlier_ratio_below_current_safety_floor")
    if coverage_p10 is not None and coverage_p10 < MIN_SPATIAL_COVERAGE:
        reasons.append("golden_spatial_coverage_below_current_safety_floor")
    if mean_error_p90 is not None and mean_error_p90 > base_max_mean:
        reasons.append("golden_mean_reprojection_error_exceeds_safety_ceiling")
    if median_error_p90 is not None and median_error_p90 > base_max_median:
        reasons.append("golden_median_reprojection_error_exceeds_safety_ceiling")
    if (
        lane == "homography"
        and homography_ratio_p90 is not None
        and homography_ratio_p90 > HOMOGRAPHY_IMPROVEMENT_RATIO
    ):
        reasons.append("golden_homography_improvement_is_too_weak")

    thresholds = None
    sufficient = not reasons
    if samples:
        match_p10 = _percentile(match_counts, 0.10) or float(MIN_MATCH_COUNT)
        inlier_count_p10 = _percentile(inlier_counts, 0.10) or float(base_min_inliers)
        thresholds = RegistrationThresholds(
            min_match_count=max(
                MIN_MATCH_COUNT,
                int(math.floor(match_p10)),
            ),
            min_inlier_count=max(
                base_min_inliers,
                int(math.floor(inlier_count_p10)),
            ),
            min_inlier_ratio=round(
                max(
                    base_min_ratio,
                    min(0.95, (inlier_p10 or base_min_ratio) - 0.03),
                ),
                6,
            ),
            min_spatial_coverage=round(
                max(
                    MIN_SPATIAL_COVERAGE,
                    min(0.95, (coverage_p10 or MIN_SPATIAL_COVERAGE) - 0.03),
                ),
                6,
            ),
            max_mean_reprojection_error=round(
                min(
                    base_max_mean,
                    (mean_error_p90 or base_max_mean) * 1.15,
                ),
                8,
            ),
            max_median_reprojection_error=round(
                min(
                    base_max_median,
                    (median_error_p90 or base_max_median) * 1.15,
                ),
                8,
            ),
            max_homography_error_ratio=(
                round(
                    min(
                        HOMOGRAPHY_IMPROVEMENT_RATIO,
                        (homography_ratio_p90 or HOMOGRAPHY_IMPROVEMENT_RATIO)
                        + 0.02,
                    ),
                    6,
                )
                if lane == "homography"
                else None
            ),
        )

    return RegistrationLaneCalibration(
        lane=lane,
        case_count=len(case_ids),
        accepted_reference_count=len(samples),
        sample_count=len(samples),
        sufficient_evidence=sufficient,
        thresholds=thresholds,
        inlier_ratio_p10=_round(inlier_p10),
        spatial_coverage_p10=_round(coverage_p10),
        mean_reprojection_error_p90=_round(mean_error_p90),
        median_reprojection_error_p90=_round(median_error_p90),
        homography_error_ratio_p90=_round(homography_ratio_p90),
        reasons=reasons,
    )


class RegistrationPolicyCalibrator:
    def __init__(self, store: HarnessStore) -> None:
        self.store = store

    def propose(
        self,
        run_ids: list[str],
        *,
        min_cases_per_lane: int = 3,
        min_quality_score: float = 0.80,
        max_manual_review_rate: float = 0.15,
    ) -> RegistrationPolicyProposal:
        if not run_ids:
            raise ValueError("registration calibration requires at least one run")
        if not 0 <= min_quality_score <= 1:
            raise ValueError("min_quality_score must be between 0 and 1")

        scorecards = []
        for run_id in run_ids:
            scorecard = self.store.get_scorecard(run_id)
            if scorecard is None:
                raise KeyError(f"scorecard not found: {run_id}")
            scorecards.append(scorecard)

        dataset_ids = {item.dataset_id for item in scorecards}
        if len(dataset_ids) != 1:
            raise ValueError("registration calibration requires one dataset")
        if any(item.tier is not BenchmarkTier.GOLDEN for item in scorecards):
            raise ValueError("registration calibration requires Golden Holdout runs")
        fingerprints = {
            item.provenance.dataset_manifest_sha256
            for item in scorecards
            if item.provenance.dataset_manifest_sha256
        }
        if len(fingerprints) > 1:
            raise ValueError("dataset manifest fingerprint mismatch")

        affine_samples: list[dict[str, float]] = []
        homography_samples: list[dict[str, float]] = []
        affine_cases: set[str] = set()
        homography_cases: set[str] = set()
        total_case_count = 0
        successful_case_count = 0
        measured_reference_count = 0
        insufficient_count = 0
        manual_count = 0
        semantic_count = 0
        blocker_case_ids: set[str] = set()
        reasons: list[str] = []

        for scorecard in scorecards:
            if scorecard.status is not HarnessRunStatus.COMPLETE:
                reasons.append(f"run_not_complete:{scorecard.run_id}")
            results = self.store.get_results(scorecard.run_id)
            total_case_count += len(results)
            for result in results:
                if result.success:
                    successful_case_count += 1
                payload = result.metadata.get("feature_correspondence")
                if not isinstance(payload, dict) or not payload:
                    blocker_case_ids.add(result.case_id)
                    continue
                try:
                    evidence = MultiReferenceCorrespondenceEvidence.model_validate(payload)
                except ValueError:
                    blocker_case_ids.add(result.case_id)
                    continue

                insufficient_count += evidence.insufficient_feature_count
                manual_count += evidence.manual_review_count
                semantic_count += evidence.semantic_required_count
                if (
                    evidence.insufficient_feature_count
                    or evidence.manual_review_count
                    or evidence.semantic_required_count
                ):
                    blocker_case_ids.add(result.case_id)

                if (
                    not result.success
                    or result.operational.manual_review
                    or result.quality_score is None
                    or result.quality_score < min_quality_score
                ):
                    continue

                for reference in evidence.references:
                    if reference.disposition not in {
                        FeatureCorrespondenceDisposition.MEASURED_AFFINE,
                        FeatureCorrespondenceDisposition.MEASURED_HOMOGRAPHY,
                    }:
                        continue
                    if (
                        reference.mean_reprojection_error is None
                        or reference.median_reprojection_error is None
                    ):
                        continue
                    sample = {
                        "match_count": float(reference.match_count),
                        "inlier_count": float(reference.inlier_count),
                        "inlier_ratio": reference.inlier_ratio,
                        "spatial_coverage": reference.spatial_coverage,
                        "mean_error": reference.mean_reprojection_error,
                        "median_error": reference.median_reprojection_error,
                    }
                    if reference.homography_error_ratio is not None:
                        sample["homography_error_ratio"] = (
                            reference.homography_error_ratio
                        )
                    measured_reference_count += 1
                    if (
                        reference.disposition
                        is FeatureCorrespondenceDisposition.MEASURED_AFFINE
                    ):
                        affine_samples.append(sample)
                        affine_cases.add(result.case_id)
                    else:
                        homography_samples.append(sample)
                        homography_cases.add(result.case_id)

        manual_review_rate = len(blocker_case_ids) / max(1, total_case_count)
        affine = _lane_calibration(
            "affine",
            affine_samples,
            affine_cases,
            min_cases=min_cases_per_lane,
        )
        homography = _lane_calibration(
            "homography",
            homography_samples,
            homography_cases,
            min_cases=min_cases_per_lane,
        )

        if blocker_case_ids and manual_review_rate > max_manual_review_rate:
            reasons.append("registration_manual_review_rate_exceeds_limit")
        if any(item.status is not HarnessRunStatus.COMPLETE for item in scorecards):
            reasons.append("registration_calibration_requires_complete_runs")
        if not affine.sufficient_evidence:
            reasons.append("affine_calibration_incomplete")
        if not homography.sufficient_evidence:
            reasons.append("homography_calibration_incomplete")

        lane_ready = affine.sufficient_evidence or homography.sufficient_evidence
        lane_safety_failed = any(
            reason.startswith("golden_")
            for lane in (affine, homography)
            for reason in lane.reasons
        )
        safety_ready = (
            manual_review_rate <= max_manual_review_rate
            and all(item.status is HarnessRunStatus.COMPLETE for item in scorecards)
            and not lane_safety_failed
        )
        sufficient = lane_ready and safety_ready

        if not safety_ready:
            recommendation = RegistrationPolicyRecommendation.MANUAL_REVIEW
        elif affine.sufficient_evidence and homography.sufficient_evidence:
            recommendation = RegistrationPolicyRecommendation.MIXED_FOR_DEWARP_BENCHMARK
        elif affine.sufficient_evidence:
            recommendation = RegistrationPolicyRecommendation.AFFINE_FOR_DEWARP_BENCHMARK
        elif homography.sufficient_evidence:
            recommendation = (
                RegistrationPolicyRecommendation.HOMOGRAPHY_FOR_DEWARP_BENCHMARK
            )
        else:
            recommendation = RegistrationPolicyRecommendation.INSUFFICIENT_EVIDENCE

        reasons.append("production_dewarp_execution_unbenchmarked")
        proposal = RegistrationPolicyProposal(
            dataset_id=next(iter(dataset_ids)),
            source_run_ids=list(run_ids),
            source_scorecard_ids=[item.scorecard_id for item in scorecards],
            source_recipe_ids=sorted({item.recipe_id for item in scorecards}),
            source_recipe_versions=sorted({item.recipe_version for item in scorecards}),
            source_tiers=sorted(
                {item.tier for item in scorecards},
                key=lambda tier: tier.value,
            ),
            dataset_manifest_sha256=next(iter(fingerprints), ""),
            total_case_count=total_case_count,
            successful_case_count=successful_case_count,
            measured_reference_count=measured_reference_count,
            insufficient_feature_count=insufficient_count,
            manual_review_count=manual_count,
            semantic_required_count=semantic_count,
            manual_review_rate=round(manual_review_rate, 8),
            max_manual_review_rate=max_manual_review_rate,
            min_cases_per_lane=min_cases_per_lane,
            affine=affine,
            homography=homography,
            recommendation=recommendation,
            sufficient_evidence=sufficient,
            requires_human_approval=True,
            automatically_applied=False,
            production_execution_enabled=False,
            reasons=list(dict.fromkeys(reasons)),
        )
        output_dir = self.store.registration_calibration_dir(proposal.proposal_id)
        self.store.save_model(output_dir / "proposal.json", proposal)
        return proposal
