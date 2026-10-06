from __future__ import annotations

from pathlib import Path

import pytest

from pod_artwork_engine.harness import HarnessStore
from pod_artwork_engine.harness_models import (
    BenchmarkCaseResult,
    BenchmarkScorecard,
    BenchmarkTier,
    HarnessRunStatus,
    OperationalMetrics,
    RegistrationPolicyRecommendation,
    RunProvenance,
)
from pod_artwork_engine.registration_calibration import RegistrationPolicyCalibrator


def _correspondence_payload(
    disposition: str,
    *,
    inlier_ratio: float = 0.82,
    spatial_coverage: float = 0.55,
    mean_error: float = 0.010,
    median_error: float = 0.008,
    homography_error_ratio: float | None = None,
) -> dict:
    return {
        "method": "feature_correspondence_benchmark_v1",
        "reference_count": 2,
        "primary_index": 0,
        "measured_indices": [0, 1],
        "excluded_indices": [],
        "measured_affine_count": int(disposition == "measured_affine"),
        "measured_homography_count": int(disposition == "measured_homography"),
        "insufficient_feature_count": 0,
        "semantic_required_count": 0,
        "manual_review_count": 0,
        "mean_inlier_ratio": inlier_ratio,
        "mean_reprojection_error": mean_error,
        "fail_closed": False,
        "execution_enabled": False,
        "references": [
            {
                "index": 0,
                "disposition": "identity",
                "model": "identity",
                "match_count": 0,
                "inlier_count": 0,
                "inlier_ratio": 1.0,
                "spatial_coverage": 1.0,
                "mean_reprojection_error": 0.0,
                "median_reprojection_error": 0.0,
                "transform_matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                "matches": [],
                "fail_closed": False,
                "execution_enabled": False,
                "reason_codes": [],
                "missing_capabilities": [],
            },
            {
                "index": 1,
                "disposition": disposition,
                "model": "homography" if disposition == "measured_homography" else "affine",
                "match_count": 10,
                "inlier_count": 8,
                "inlier_ratio": inlier_ratio,
                "spatial_coverage": spatial_coverage,
                "mean_reprojection_error": mean_error,
                "median_reprojection_error": median_error,
                "affine_inlier_ratio": 0.76,
                "affine_mean_reprojection_error": 0.014,
                "affine_median_reprojection_error": 0.012,
                "homography_inlier_ratio": 0.84,
                "homography_mean_reprojection_error": 0.009,
                "homography_median_reprojection_error": 0.007,
                "homography_error_ratio": homography_error_ratio,
                "transform_matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                "matches": [],
                "fail_closed": False,
                "execution_enabled": False,
                "reason_codes": [],
                "missing_capabilities": [],
            },
        ],
        "reason_codes": [],
    }


def _write_run(
    store: HarnessStore,
    run_id: str,
    *,
    tier: BenchmarkTier = BenchmarkTier.GOLDEN,
    disposition: str = "measured_affine",
    cases: int = 3,
    inlier_ratio: float = 0.82,
    mean_error: float = 0.010,
    median_error: float = 0.008,
    homography_error_ratio: float | None = None,
    manual_review: bool = False,
) -> None:
    scorecard = BenchmarkScorecard(
        run_id=run_id,
        dataset_id="dataset-1",
        tier=tier,
        recipe_id="local-precision-v19",
        recipe_version="19",
        status=HarnessRunStatus.COMPLETE,
        case_count=cases,
        success_count=cases,
        failure_count=0,
        manual_review_count=int(manual_review) * cases,
        quality_mean=0.92,
        semantic_mean=0.92,
        technical_mean=0.92,
        provenance=RunProvenance(dataset_manifest_sha256="abc123"),
    )
    store.save_model(store.scorecard_path(run_id), scorecard)
    results_dir = store.run_dir(run_id) / "results"
    for index in range(cases):
        result = BenchmarkCaseResult(
            case_id=f"golden-case-{index}",
            pair_id=f"pair-{index}",
            artwork_identity=f"art-{index}",
            success=True,
            quality_score=0.92,
            operational=OperationalMetrics(manual_review=manual_review),
            metadata={
                "feature_correspondence": _correspondence_payload(
                    disposition,
                    inlier_ratio=inlier_ratio,
                    mean_error=mean_error,
                    median_error=median_error,
                    homography_error_ratio=homography_error_ratio,
                )
            },
        )
        store.save_model(results_dir / f"case-{index}.json", result)


def test_affine_golden_calibration_proposes_benchmark_lane(tmp_path: Path) -> None:
    store = HarnessStore(tmp_path / "harness")
    _write_run(store, "run-affine")

    proposal = RegistrationPolicyCalibrator(store).propose(["run-affine"])

    assert proposal.sufficient_evidence is True
    assert proposal.affine.sufficient_evidence is True
    assert proposal.affine.thresholds is not None
    assert proposal.recommendation is (
        RegistrationPolicyRecommendation.AFFINE_FOR_DEWARP_BENCHMARK
    )
    assert proposal.requires_human_approval is True
    assert proposal.automatically_applied is False
    assert proposal.production_execution_enabled is False
    assert (store.registration_calibration_dir(proposal.proposal_id) / "proposal.json").is_file()


def test_homography_golden_calibration_uses_measured_improvement(tmp_path: Path) -> None:
    store = HarnessStore(tmp_path / "harness")
    _write_run(
        store,
        "run-homography",
        disposition="measured_homography",
        homography_error_ratio=0.58,
    )

    proposal = RegistrationPolicyCalibrator(store).propose(["run-homography"])

    assert proposal.sufficient_evidence is True
    assert proposal.homography.sufficient_evidence is True
    assert proposal.homography.thresholds is not None
    assert proposal.homography.thresholds.max_homography_error_ratio is not None
    assert proposal.homography.thresholds.max_homography_error_ratio <= 0.82
    assert proposal.recommendation is (
        RegistrationPolicyRecommendation.HOMOGRAPHY_FOR_DEWARP_BENCHMARK
    )


def test_insufficient_golden_cases_do_not_promote_lane(tmp_path: Path) -> None:
    store = HarnessStore(tmp_path / "harness")
    _write_run(store, "run-small", cases=1)

    proposal = RegistrationPolicyCalibrator(store).propose(["run-small"])

    assert proposal.sufficient_evidence is False
    assert proposal.affine.sufficient_evidence is False
    assert "insufficient_golden_cases" in proposal.affine.reasons
    assert proposal.production_execution_enabled is False


def test_bad_registration_distribution_forces_manual_review(tmp_path: Path) -> None:
    store = HarnessStore(tmp_path / "harness")
    _write_run(
        store,
        "run-bad",
        inlier_ratio=0.42,
        mean_error=0.050,
        median_error=0.040,
    )

    proposal = RegistrationPolicyCalibrator(store).propose(["run-bad"])

    assert proposal.sufficient_evidence is False
    assert proposal.recommendation is RegistrationPolicyRecommendation.MANUAL_REVIEW
    assert any(reason.startswith("golden_") for reason in proposal.affine.reasons)
    assert proposal.production_execution_enabled is False


def test_non_golden_run_is_rejected(tmp_path: Path) -> None:
    store = HarnessStore(tmp_path / "harness")
    _write_run(store, "run-regression", tier=BenchmarkTier.REGRESSION)

    with pytest.raises(ValueError, match="Golden Holdout"):
        RegistrationPolicyCalibrator(store).propose(["run-regression"])
