from __future__ import annotations

from pathlib import Path

from pod_artwork_engine.contracts import ArtworkType, QualityMode, RouteKind
from pod_artwork_engine.dataset_registry import DatasetRegistry
from pod_artwork_engine.harness import HarnessStore
from pod_artwork_engine.harness_models import (
    BenchmarkCaseResult,
    BenchmarkRecipe,
    BenchmarkScorecard,
    BenchmarkTier,
    HarnessRunStatus,
    OperationalMetrics,
    RouteEvidence,
    RouteCaseComparison,
    RouteMatrixReport,
    RouteMatrixSpec,
    RoutePreference,
    RunProvenance,
)
from pod_artwork_engine.route_matrix import RouteMatrixRunner
from pod_artwork_engine.router_calibration import RouterPolicyCalibrator
from pod_artwork_engine.router_policy import DEFAULT_ROUTER_POLICY
from pod_artwork_engine.settings import Settings


def _write_recipe(path: Path) -> Path:
    recipe = BenchmarkRecipe(recipe_id="base-recipe", version="1")
    path.write_text(recipe.model_dump_json(indent=2), encoding="utf-8")
    return path


def test_route_matrix_compares_actual_remote_execution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = Settings(data_root=tmp_path / "runtime")
    registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
    store = HarnessStore(settings.harness_dir)
    recipe_path = _write_recipe(tmp_path / "recipe.json")

    def fake_run(
        self,
        dataset_id,
        tier,
        recipe,
        *,
        quality_mode,
        limit=None,
        recipe_base=None,
    ):
        route = RouteKind(recipe.metadata["harness_route_override"])
        run_id = f"run-{route.value}"
        scorecard = BenchmarkScorecard(
            run_id=run_id,
            dataset_id=dataset_id,
            tier=tier,
            recipe_id=recipe.recipe_id,
            recipe_version=recipe.version,
            status=HarnessRunStatus.COMPLETE,
            case_count=2,
            success_count=2,
            failure_count=0,
            manual_review_count=0,
            quality_mean=0.80,
            provenance=RunProvenance(
                execution_kind="production_engine",
                quality_mode=quality_mode,
                dataset_manifest_sha256="dataset-fixture",
                router_policy_id=DEFAULT_ROUTER_POLICY.policy_id,
                router_policy_version=DEFAULT_ROUTER_POLICY.version,
                route_override=route,
            ),
        )
        store.save_model(store.scorecard_path(run_id), scorecard)

        qualities = (
            {"pair-a": 0.80, "pair-b": 0.90}
            if route is RouteKind.DETERMINISTIC
            else {"pair-a": 0.92, "pair-b": 0.72}
        )
        for pair_id, quality in qualities.items():
            store.save_model(
                store.run_dir(run_id) / "results" / f"{pair_id}.json",
                BenchmarkCaseResult(
                    case_id=pair_id,
                    pair_id=pair_id,
                    artwork_identity=pair_id,
                    success=True,
                    quality_score=quality,
                    operational=OperationalMetrics(latency_ms=1000),
                    route=RouteEvidence(
                        selected_route=route,
                        requested_override=route,
                        used_remote=route is not RouteKind.DETERMINISTIC,
                        remote_available=True,
                        design_confidence=0.60 if pair_id == "pair-a" else 0.80,
                        artwork_type=ArtworkType.LOGO,
                    ),
                ),
            )
        return scorecard

    monkeypatch.setattr(
        "pod_artwork_engine.route_matrix.HarnessEngineRunner.run",
        fake_run,
    )

    report = RouteMatrixRunner(settings, registry, store).run(
        RouteMatrixSpec(
            dataset_id="dataset-fixture",
            tier=BenchmarkTier.GOLDEN,
            recipe_path=str(recipe_path),
            routes=[RouteKind.DETERMINISTIC, RouteKind.HYBRID],
            min_quality_gain=0.02,
        )
    )

    assert report.comparable_case_count == 2
    assert report.remote_preferred_count == 1
    assert report.deterministic_preferred_count == 1
    by_pair = {item.pair_id: item for item in report.comparisons}
    assert by_pair["pair-a"].preference is RoutePreference.REMOTE
    assert by_pair["pair-b"].preference is RoutePreference.DETERMINISTIC
    assert report.auto_applied is False
    assert (store.route_matrix_dir(report.matrix_id) / "report.json").is_file()


def _save_router_matrix_fixture(
    store: HarnessStore,
    *,
    matrix_id: str,
    tier: BenchmarkTier = BenchmarkTier.GOLDEN,
) -> None:
    comparisons = [
        RouteCaseComparison(
            pair_id="remote-logo",
            artwork_identity="remote-logo",
            design_confidence=0.58,
            artwork_type=ArtworkType.LOGO,
            deterministic_quality=0.70,
            remote_quality=0.90,
            quality_delta=0.20,
            remote_route=RouteKind.HYBRID,
            remote_used=True,
            preference=RoutePreference.REMOTE,
        ),
        RouteCaseComparison(
            pair_id="det-logo",
            artwork_identity="det-logo",
            design_confidence=0.78,
            artwork_type=ArtworkType.LOGO,
            deterministic_quality=0.93,
            remote_quality=0.90,
            quality_delta=-0.03,
            remote_route=RouteKind.HYBRID,
            remote_used=True,
            preference=RoutePreference.DETERMINISTIC,
        ),
    ]
    report = RouteMatrixReport(
        matrix_id=matrix_id,
        dataset_id="dataset-v1",
        tier=tier,
        recipe_id="base",
        recipe_version="1",
        quality_mode=QualityMode.PRINT_READY,
        min_quality_gain=0.02,
        dataset_manifest_sha256="dataset-fingerprint",
        router_policy_id=DEFAULT_ROUTER_POLICY.policy_id,
        router_policy_version=DEFAULT_ROUTER_POLICY.version,
        comparisons=comparisons,
        comparable_case_count=2,
        remote_preferred_count=1,
        deterministic_preferred_count=1,
    )
    store.save_model(store.route_matrix_dir(matrix_id) / "report.json", report)


def test_router_calibration_uses_counterfactual_evidence_and_stays_review_only(
    tmp_path: Path,
) -> None:
    store = HarnessStore(tmp_path / "harness")
    _save_router_matrix_fixture(store, matrix_id="golden-matrix")

    proposal = RouterPolicyCalibrator(
        store,
        DEFAULT_ROUTER_POLICY,
    ).propose(
        ["golden-matrix"],
        min_remote=1,
        min_deterministic=1,
        max_false_local_rate=0.0,
    )

    metric = proposal.metrics["deterministic_text_logo_min_confidence"]
    assert metric.sufficient_evidence is True
    assert metric.recommended_threshold is not None
    assert metric.recommended_threshold > 0.58
    assert proposal.candidate_policy.deterministic_text_logo_min_confidence > 0.58
    assert proposal.sufficient_evidence is True
    assert proposal.requires_human_approval is True
    assert proposal.automatically_applied is False
    output_dir = store.router_calibration_dir(proposal.proposal_id)
    assert (output_dir / "proposal.json").is_file()
    assert (output_dir / "candidate-router-policy.json").is_file()


def test_router_calibration_requires_golden_by_default(tmp_path: Path) -> None:
    store = HarnessStore(tmp_path / "harness")
    _save_router_matrix_fixture(
        store,
        matrix_id="regression-matrix",
        tier=BenchmarkTier.REGRESSION,
    )

    proposal = RouterPolicyCalibrator(
        store,
        DEFAULT_ROUTER_POLICY,
    ).propose(
        ["regression-matrix"],
        min_remote=1,
        min_deterministic=1,
        max_false_local_rate=0.0,
    )

    assert proposal.sufficient_evidence is False
    assert any("Golden Holdout" in reason for reason in proposal.reasons)
