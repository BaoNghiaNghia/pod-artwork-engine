from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pod_artwork_engine.dewarp_experiment import (
    DewarpExperimentError,
    DewarpExperimentRunner,
    build_production_registration_policy_proposal,
)
from pod_artwork_engine.harness import HarnessStore
from pod_artwork_engine.harness_models import (
    BenchmarkTier,
    DewarpBenchmarkRecommendation,
    DewarpBenchmarkReport,
    DewarpExperimentSpec,
    DewarpMaterializationReport,
    ProductionRegistrationPolicyRecommendation,
    RegistrationLaneCalibration,
    RegistrationPolicyProposal,
    RegistrationPolicyRecommendation,
    RegistrationThresholds,
)
from pod_artwork_engine.settings import Settings


FINGERPRINT = "a" * 64


def _lane(name: str, *, sufficient: bool = True) -> RegistrationLaneCalibration:
    thresholds = RegistrationThresholds(
        min_match_count=6,
        min_inlier_count=5 if name == "affine" else 6,
        min_inlier_ratio=0.70,
        min_spatial_coverage=0.25,
        max_mean_reprojection_error=0.02,
        max_median_reprojection_error=0.015,
        max_homography_error_ratio=(0.78 if name == "homography" else None),
    )
    return RegistrationLaneCalibration(
        lane=name,
        case_count=5 if sufficient else 1,
        accepted_reference_count=5 if sufficient else 1,
        sample_count=5 if sufficient else 1,
        sufficient_evidence=sufficient,
        thresholds=thresholds,
        reasons=[] if sufficient else ["insufficient_golden_cases"],
    )


def _calibration(
    *,
    sufficient: bool = True,
    recommendation: RegistrationPolicyRecommendation = (
        RegistrationPolicyRecommendation.AFFINE_FOR_DEWARP_BENCHMARK
    ),
    fingerprint: str = FINGERPRINT,
) -> RegistrationPolicyProposal:
    return RegistrationPolicyProposal(
        proposal_id="registration-policy-test",
        dataset_id="dataset-v1",
        source_run_ids=["run-golden-1"],
        source_scorecard_ids=["score-golden-1"],
        source_recipe_ids=["local-precision-v21"],
        source_recipe_versions=["21"],
        source_tiers=[BenchmarkTier.GOLDEN],
        dataset_manifest_sha256=fingerprint,
        total_case_count=10,
        successful_case_count=10,
        measured_reference_count=10,
        affine=_lane("affine", sufficient=sufficient),
        homography=_lane("homography", sufficient=False),
        recommendation=recommendation,
        sufficient_evidence=sufficient,
        requires_human_approval=True,
        automatically_applied=False,
        production_execution_enabled=False,
    )


def _spec(**updates) -> DewarpExperimentSpec:
    base = DewarpExperimentSpec(
        experiment_id="dewarp-experiment-test",
        dataset_id="dataset-v1",
        registration_run_ids=["run-golden-1"],
        recipe_path="recipe.json",
        materialization_source_run_id="run-golden-1",
        min_comparable_cases=3,
        min_quality_gain=0.005,
        min_technical_gain=0.0,
        min_small_detail_delta=0.0,
        min_region_improvement_rate=0.60,
        max_materialization_failure_rate=0.10,
        max_failure_rate_increase=0.0,
        max_manual_review_rate_increase=0.0,
    )
    return base.model_copy(update=updates)


def _cohort(
    *,
    fingerprint: str = FINGERPRINT,
    success_count: int = 10,
    failure_count: int = 0,
    improved_count: int = 8,
) -> DewarpMaterializationReport:
    return DewarpMaterializationReport(
        cohort_id="dewarp-experiment-test_cohort",
        dataset_id="dataset-v1",
        source_run_id="run-golden-1",
        registration_policy_id="registration-policy-test",
        tier=BenchmarkTier.GOLDEN,
        dataset_manifest_sha256=fingerprint,
        native_manifest_path="native.json",
        dewarp_manifest_path="dewarp.json",
        case_count=success_count + failure_count,
        success_count=success_count,
        failure_count=failure_count,
        affine_count=success_count,
        homography_count=0,
        region_improved_case_count=improved_count,
        benchmark_only=True,
        requires_human_approval=True,
        automatically_applied=False,
        production_execution_enabled=False,
    )


def _matrix(
    *,
    fingerprint: str = FINGERPRINT,
    recommendation: DewarpBenchmarkRecommendation = (
        DewarpBenchmarkRecommendation.DEWARP_FOR_HUMAN_REVIEW
    ),
    sufficient: bool = True,
    quality_delta: float = 0.02,
    technical_delta: float = 0.01,
    detail_delta: float = 0.01,
    failure_delta: float = 0.0,
    manual_delta: float = 0.0,
) -> DewarpBenchmarkReport:
    return DewarpBenchmarkReport(
        matrix_id="dewarp-experiment-test_matrix",
        dataset_id="dataset-v1",
        tier=BenchmarkTier.GOLDEN,
        recipe_id="local-precision-v21",
        recipe_version="21",
        dataset_manifest_sha256=fingerprint,
        native_run_id="run-native",
        dewarp_run_id="run-dewarp",
        native_scorecard_id="score-native",
        dewarp_scorecard_id="score-dewarp",
        comparable_case_count=10,
        quality_delta=quality_delta,
        technical_delta=technical_delta,
        small_detail_delta=detail_delta,
        failure_rate_delta=failure_delta,
        manual_review_rate_delta=manual_delta,
        recommendation=recommendation,
        sufficient_evidence=sufficient,
        benchmark_only=True,
        requires_human_approval=True,
        automatically_applied=False,
        production_execution_enabled=False,
    )


def test_successful_golden_evidence_becomes_human_approval_candidate() -> None:
    proposal = build_production_registration_policy_proposal(
        _spec(),
        _calibration(),
        cohort=_cohort(),
        matrix=_matrix(),
        materialization_source_run_id="run-golden-1",
    )

    assert (
        proposal.recommendation
        is ProductionRegistrationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
    )
    assert proposal.sufficient_evidence is True
    assert proposal.region_improvement_rate == pytest.approx(0.8)
    assert proposal.affine_thresholds is not None
    assert proposal.homography_thresholds is None
    assert proposal.requires_human_approval is True
    assert proposal.automatically_applied is False
    assert proposal.production_execution_enabled is False


def test_insufficient_calibration_keeps_experiment_evidence_only() -> None:
    proposal = build_production_registration_policy_proposal(
        _spec(),
        _calibration(
            sufficient=False,
            recommendation=RegistrationPolicyRecommendation.INSUFFICIENT_EVIDENCE,
        ),
        materialization_source_run_id=None,
    )

    assert (
        proposal.recommendation
        is ProductionRegistrationPolicyRecommendation.INSUFFICIENT_EVIDENCE
    )
    assert proposal.sufficient_evidence is False
    assert proposal.production_execution_enabled is False
    assert "registration_calibration_insufficient" in proposal.reasons


def test_safety_regression_forces_manual_review() -> None:
    proposal = build_production_registration_policy_proposal(
        _spec(),
        _calibration(),
        cohort=_cohort(),
        matrix=_matrix(
            technical_delta=-0.01,
            detail_delta=-0.02,
            failure_delta=0.05,
        ),
        materialization_source_run_id="run-golden-1",
    )

    assert (
        proposal.recommendation
        is ProductionRegistrationPolicyRecommendation.MANUAL_REVIEW
    )
    assert proposal.sufficient_evidence is False
    assert "dewarp_technical_regression" in proposal.reasons
    assert "dewarp_small_detail_regression" in proposal.reasons
    assert "dewarp_failure_rate_regression" in proposal.reasons


def test_measured_but_unhelpful_dewarp_stays_disabled() -> None:
    proposal = build_production_registration_policy_proposal(
        _spec(),
        _calibration(),
        cohort=_cohort(improved_count=4),
        matrix=_matrix(
            recommendation=DewarpBenchmarkRecommendation.KEEP_NATIVE,
            quality_delta=0.001,
        ),
        materialization_source_run_id="run-golden-1",
    )

    assert (
        proposal.recommendation
        is ProductionRegistrationPolicyRecommendation.KEEP_DISABLED
    )
    assert proposal.sufficient_evidence is True
    assert proposal.production_execution_enabled is False


def test_non_golden_experiment_is_rejected(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.json"
    recipe.write_text(
        json.dumps({"recipe_id": "test", "version": "21", "stages": []}),
        encoding="utf-8",
    )
    settings = Settings(data_root=tmp_path / "runtime")
    settings.ensure_directories()
    runner = DewarpExperimentRunner(
        settings,
        object(),
        HarnessStore(settings.harness_dir),
    )

    with pytest.raises(DewarpExperimentError, match="Golden Holdout"):
        runner.run(
            _spec(
                tier=BenchmarkTier.REGRESSION,
                recipe_path=str(recipe),
            )
        )


def test_fingerprint_mismatch_is_rejected() -> None:
    with pytest.raises(DewarpExperimentError, match="fingerprint"):
        build_production_registration_policy_proposal(
            _spec(),
            _calibration(),
            cohort=_cohort(fingerprint="b" * 64),
            matrix=_matrix(),
            materialization_source_run_id="run-golden-1",
        )


def test_experiment_persists_spec_report_and_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.json"
    recipe.write_text(
        json.dumps(
            {
                "recipe_id": "local-precision-v21",
                "version": "21",
                "stages": [],
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(data_root=tmp_path / "runtime")
    settings.ensure_directories()
    store = HarnessStore(settings.harness_dir)
    calibration = _calibration()
    cohort = _cohort()
    matrix = _matrix()
    calibration_path = (
        store.registration_calibration_dir(calibration.proposal_id)
        / "proposal.json"
    )
    store.save_model(calibration_path, calibration)

    monkeypatch.setattr(
        "pod_artwork_engine.dewarp_experiment.RegistrationPolicyCalibrator.propose",
        lambda self, run_ids, **kwargs: calibration,
    )
    monkeypatch.setattr(
        "pod_artwork_engine.dewarp_experiment.DewarpMaterializer.materialize",
        lambda self, spec: cohort,
    )
    monkeypatch.setattr(
        "pod_artwork_engine.dewarp_experiment.DewarpBenchmarkMatrixRunner.run",
        lambda self, spec, **kwargs: matrix,
    )

    spec = _spec(recipe_path=str(recipe))
    report = DewarpExperimentRunner(
        settings,
        object(),
        store,
    ).run(spec)

    experiment_dir = store.dewarp_experiment_dir(spec.experiment_id)
    assert (experiment_dir / "spec.json").is_file()
    assert (experiment_dir / "report.json").is_file()
    assert (experiment_dir / "production-policy-proposal.json").is_file()
    assert report.policy.production_execution_enabled is False
    assert (
        report.policy.recommendation
        is ProductionRegistrationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
    )


def test_runner_skips_dewarp_when_calibration_is_insufficient(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.json"
    recipe.write_text(
        json.dumps({"recipe_id": "test-v21", "version": "21", "stages": []}),
        encoding="utf-8",
    )
    settings = Settings(data_root=tmp_path / "runtime-insufficient")
    settings.ensure_directories()
    store = HarnessStore(settings.harness_dir)
    calibration = _calibration(
        sufficient=False,
        recommendation=RegistrationPolicyRecommendation.INSUFFICIENT_EVIDENCE,
    )
    calibration_path = (
        store.registration_calibration_dir(calibration.proposal_id)
        / "proposal.json"
    )
    store.save_model(calibration_path, calibration)

    monkeypatch.setattr(
        "pod_artwork_engine.dewarp_experiment.RegistrationPolicyCalibrator.propose",
        lambda self, run_ids, **kwargs: calibration,
    )

    def forbidden_materialize(self, spec):
        raise AssertionError("dewarp materialization must not run")

    def forbidden_matrix(self, spec, **kwargs):
        raise AssertionError("dewarp matrix must not run")

    monkeypatch.setattr(
        "pod_artwork_engine.dewarp_experiment.DewarpMaterializer.materialize",
        forbidden_materialize,
    )
    monkeypatch.setattr(
        "pod_artwork_engine.dewarp_experiment.DewarpBenchmarkMatrixRunner.run",
        forbidden_matrix,
    )

    spec = _spec(
        experiment_id="insufficient-experiment",
        recipe_path=str(recipe),
        materialization_source_run_id=None,
    )
    report = DewarpExperimentRunner(settings, object(), store).run(spec)

    assert report.cohort_id is None
    assert report.matrix_id is None
    assert (
        report.policy.recommendation
        is ProductionRegistrationPolicyRecommendation.INSUFFICIENT_EVIDENCE
    )
    assert report.policy.production_execution_enabled is False


def test_cli_exposes_dewarp_experiment_command() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "pod_artwork_engine", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "harness-dewarp-experiment" in completed.stdout
