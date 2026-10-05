from __future__ import annotations

from pathlib import Path

from pod_artwork_engine.benchmark_suite import BenchmarkSuiteRunner
from pod_artwork_engine.calibration import QCPolicyCalibrator
from pod_artwork_engine.contracts import QualityMode
from pod_artwork_engine.dataset_registry import DatasetRegistry
from pod_artwork_engine.harness import HarnessStore
from pod_artwork_engine.harness_models import (
    BenchmarkCaseResult,
    BenchmarkRecipe,
    BenchmarkScorecard,
    BenchmarkSuiteSpec,
    BenchmarkTier,
    HarnessRunStatus,
    RunProvenance,
    RuntimeQCEvidence,
    SuiteRecommendation,
)
from pod_artwork_engine.qc_policy import DEFAULT_QC_POLICY
from pod_artwork_engine.settings import Settings


def _write_recipe(path: Path, recipe_id: str) -> Path:
    recipe = BenchmarkRecipe(recipe_id=recipe_id, version="1")
    path.write_text(recipe.model_dump_json(indent=2), encoding="utf-8")
    return path


def _scorecard(
    *,
    dataset_id: str,
    tier: BenchmarkTier,
    recipe_id: str,
    quality: float,
) -> BenchmarkScorecard:
    return BenchmarkScorecard(
        run_id=f"run-{recipe_id}-{tier.value}",
        dataset_id=dataset_id,
        tier=tier,
        recipe_id=recipe_id,
        recipe_version="1",
        status=HarnessRunStatus.COMPLETE,
        case_count=5,
        success_count=5,
        failure_count=0,
        manual_review_count=0,
        quality_mean=quality,
        semantic_mean=quality,
        technical_mean=quality,
        provenance=RunProvenance(
            execution_kind="production_engine",
            quality_mode=QualityMode.PRINT_READY,
        ),
    )


def test_suite_runs_smoke_regression_golden_and_never_auto_promotes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = Settings(data_root=tmp_path / "runtime")
    registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
    store = HarnessStore(settings.harness_dir)
    champion_path = _write_recipe(tmp_path / "champion.json", "champion")
    challenger_path = _write_recipe(tmp_path / "challenger.json", "challenger")
    runner = BenchmarkSuiteRunner(settings, registry, store)

    def fake_run(recipe, recipe_path, spec, tier):
        quality = 0.90 if recipe.recipe_id == "champion" else 0.91
        return _scorecard(
            dataset_id=spec.dataset_id,
            tier=tier,
            recipe_id=recipe.recipe_id,
            quality=quality,
        )

    monkeypatch.setattr(runner, "_run_recipe", fake_run)
    spec = BenchmarkSuiteSpec(
        dataset_id="historical-v1",
        champion_recipe_path=str(champion_path),
        challenger_recipe_paths=[str(challenger_path)],
    )

    report = runner.run(spec)

    assert report.auto_promoted is False
    assert report.requires_human_approval is True
    assert len(report.challengers) == 1
    challenger = report.challengers[0]
    assert [item.tier for item in challenger.tiers] == [
        BenchmarkTier.SMOKE,
        BenchmarkTier.REGRESSION,
        BenchmarkTier.GOLDEN,
    ]
    assert all(item.passed_gate for item in challenger.tiers)
    assert (
        challenger.recommendation
        is SuiteRecommendation.ELIGIBLE_FOR_HUMAN_REVIEW
    )
    assert (store.suite_dir(spec.suite_id) / "report.json").is_file()


def test_suite_stops_challenger_after_regression_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = Settings(data_root=tmp_path / "runtime")
    registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
    store = HarnessStore(settings.harness_dir)
    champion_path = _write_recipe(tmp_path / "champion.json", "champion")
    challenger_path = _write_recipe(tmp_path / "challenger.json", "challenger")
    runner = BenchmarkSuiteRunner(settings, registry, store)

    def fake_run(recipe, recipe_path, spec, tier):
        if recipe.recipe_id == "champion":
            quality = 0.90
        elif tier is BenchmarkTier.SMOKE:
            quality = 0.91
        else:
            quality = 0.80
        return _scorecard(
            dataset_id=spec.dataset_id,
            tier=tier,
            recipe_id=recipe.recipe_id,
            quality=quality,
        )

    monkeypatch.setattr(runner, "_run_recipe", fake_run)
    report = runner.run(
        BenchmarkSuiteSpec(
            dataset_id="historical-v1",
            champion_recipe_path=str(champion_path),
            challenger_recipe_paths=[str(challenger_path)],
        )
    )

    challenger = report.challengers[0]
    assert [item.tier for item in challenger.tiers] == [
        BenchmarkTier.SMOKE,
        BenchmarkTier.REGRESSION,
    ]
    assert challenger.recommendation is SuiteRecommendation.REJECTED


def _store_calibration_fixture(
    store: HarnessStore,
    *,
    run_id: str,
    tier: BenchmarkTier = BenchmarkTier.GOLDEN,
) -> None:
    scorecard = BenchmarkScorecard(
        run_id=run_id,
        dataset_id="dataset-calibration",
        tier=tier,
        recipe_id="champion",
        recipe_version="1",
        status=HarnessRunStatus.COMPLETE,
        case_count=4,
        success_count=4,
        failure_count=0,
        manual_review_count=0,
        quality_mean=0.75,
        provenance=RunProvenance(
            execution_kind="production_engine",
            quality_mode=QualityMode.PRINT_READY,
            dataset_manifest_sha256="fixture-dataset",
            qc_policy_id=DEFAULT_QC_POLICY.policy_id,
            qc_policy_version=DEFAULT_QC_POLICY.version,
        ),
    )
    store.save_model(store.scorecard_path(run_id), scorecard)

    fixtures = [
        ("good-a", 0.92, 0.45, 0.62),
        ("good-b", 0.90, 0.50, 0.65),
        ("bad-a", 0.55, 0.40, 0.58),
        ("bad-b", 0.50, 0.42, 0.60),
    ]
    results_dir = store.run_dir(run_id) / "results"
    for case_id, quality, semantic_score, resolution_score in fixtures:
        result = BenchmarkCaseResult(
            case_id=case_id,
            pair_id=case_id,
            artwork_identity=case_id,
            success=True,
            quality_score=quality,
            runtime_qc=RuntimeQCEvidence(
                semantic_score=semantic_score,
                resolution_score=resolution_score,
            ),
        )
        store.save_model(results_dir / f"{case_id}.json", result)


def test_calibration_proposal_is_bounded_review_only_and_writes_candidate(
    tmp_path: Path,
) -> None:
    store = HarnessStore(tmp_path / "harness")
    _store_calibration_fixture(store, run_id="golden-run")

    proposal = QCPolicyCalibrator(
        store,
        DEFAULT_QC_POLICY,
    ).propose(
        ["golden-run"],
        min_good=2,
        min_bad=2,
        max_false_accept_rate=0.0,
    )

    assert proposal.sufficient_evidence is True
    assert proposal.requires_human_approval is True
    assert proposal.automatically_applied is False
    assert proposal.proposed_policy.semantic_min_score == 0.45
    assert proposal.proposed_policy.min_resolution_score == 0.62
    assert proposal.proposed_policy.object_fidelity_min == 0.60
    assert proposal.candidate_policy.print_ready.semantic_min_score == 0.45
    output_dir = store.calibration_dir(proposal.proposal_id)
    assert (output_dir / "proposal.json").is_file()
    assert (output_dir / "candidate-qc-policy.json").is_file()


def test_calibration_requires_golden_by_default(tmp_path: Path) -> None:
    store = HarnessStore(tmp_path / "harness")
    _store_calibration_fixture(
        store,
        run_id="regression-run",
        tier=BenchmarkTier.REGRESSION,
    )

    proposal = QCPolicyCalibrator(
        store,
        DEFAULT_QC_POLICY,
    ).propose(
        ["regression-run"],
        min_good=2,
        min_bad=2,
        max_false_accept_rate=0.0,
    )

    assert proposal.sufficient_evidence is False
    assert any("Golden Holdout" in reason for reason in proposal.reasons)
