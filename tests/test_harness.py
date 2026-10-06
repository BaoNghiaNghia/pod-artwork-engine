from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from pod_artwork_engine.contracts import QualityMode, SemanticJudgeResult
from pod_artwork_engine.dataset_registry import DatasetRegistry
from pod_artwork_engine.harness import (
    HarnessCaseFactory,
    HarnessRunner,
    HarnessStore,
    compare_scorecards,
)
from pod_artwork_engine.harness_engine import HarnessEngineRunner
from pod_artwork_engine.harness_metrics import aggregate_metric_scores, evaluate_images
from pod_artwork_engine.harness_models import (
    BenchmarkRecipe,
    BenchmarkScorecard,
    BenchmarkTier,
    CandidateManifest,
    CandidateManifestEntry,
    CohortScore,
    HarnessRunStatus,
    OperationalMetrics,
    PrecisionEvidence,
    PromotionPolicy,
    RecipeStage,
    RunProvenance,
)
from pod_artwork_engine.settings import Settings


def _art(path: Path, color: tuple[int, int, int], offset: int = 0) -> Path:
    image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((16 + offset, 18, 78, 72), fill=(*color, 255))
    draw.ellipse((32, 30 + offset, 64, 62 + offset), fill=(255, 255, 255, 180))
    image.save(path)
    return path


def _seed_dataset(tmp_path: Path, count: int = 10) -> tuple[DatasetRegistry, str]:
    registry = DatasetRegistry(tmp_path / "engine.sqlite3", tmp_path / "datasets")
    source = _art(tmp_path / "source.png", (40, 80, 120))

    for index in range(count):
        target = _art(
            tmp_path / f"target-{index}.png",
            (
                (40 + index * 29) % 255,
                (80 + index * 43) % 255,
                (120 + index * 67) % 255,
            ),
            offset=index % 4,
        )
        registry.register_pair(
            f"design-{index}",
            [source],
            target,
            metadata={
                "artwork_type": "logo" if index % 2 == 0 else "mixed",
                "difficulty": "easy" if index < 5 else "hard",
                "exact_text": [f"DESIGN {index}"],
                "constraints": {"transparent": True},
            },
        )

    dataset = registry.create_dataset("harness-fixture", seed="harness-seed")
    return registry, dataset.dataset_id


def test_metrics_identical_image_scores_near_one(tmp_path: Path) -> None:
    target = _art(tmp_path / "target.png", (120, 50, 210))
    semantic, technical = evaluate_images(
        target,
        target,
        expected_text=["HELLO"],
        recognized_text=["HELLO"],
    )
    semantic_score, technical_score, quality_score = aggregate_metric_scores(
        semantic,
        technical,
    )

    assert semantic.exact_text == 1.0
    assert semantic.color == 1.0
    assert technical.edge == 1.0
    assert technical.alpha == 1.0
    assert semantic_score is not None and semantic_score > 0.99
    assert technical_score is not None and technical_score > 0.99
    assert quality_score is not None and quality_score > 0.99


def test_harness_tiers_follow_dataset_splits(tmp_path: Path) -> None:
    registry, dataset_id = _seed_dataset(tmp_path)
    factory = HarnessCaseFactory(registry)

    smoke = factory.build(dataset_id, BenchmarkTier.SMOKE)
    regression = factory.build(dataset_id, BenchmarkTier.REGRESSION)
    golden = factory.build(dataset_id, BenchmarkTier.GOLDEN)

    assert smoke
    assert regression
    assert golden
    assert len(smoke) <= 8

    train_ids = {
        member.pair_id
        for member in registry.list_members(dataset_id)
        if member.split.value == "train"
    }
    validation_ids = {
        member.pair_id
        for member in registry.list_members(dataset_id)
        if member.split.value == "validation"
    }
    golden_ids = {
        member.pair_id
        for member in registry.list_members(dataset_id)
        if member.split.value == "golden_holdout"
    }

    assert {case.pair_id for case in smoke} <= train_ids
    assert {case.pair_id for case in regression} == validation_ids
    assert {case.pair_id for case in golden} == golden_ids
    assert all(case.source_paths and case.target_path for case in golden)


def test_runner_writes_scorecard_results_and_visual_diff(tmp_path: Path) -> None:
    registry, dataset_id = _seed_dataset(tmp_path)
    store = HarnessStore(tmp_path / "harness")
    cases = HarnessCaseFactory(registry).build(dataset_id, BenchmarkTier.SMOKE, limit=2)
    assert cases

    manifest = CandidateManifest(
        candidates={
            case.pair_id: CandidateManifestEntry(
                result_path=case.target_path,
                recognized_text=case.exact_text,
                semantic_judge=SemanticJudgeResult(
                    object_fidelity=0.99,
                    confidence=0.98,
                ),
                operational=OperationalMetrics(
                    latency_ms=1200,
                    provider_calls=1,
                    cost_usd=0.01,
                ),
                precision=PrecisionEvidence(
                    local_ocr=True,
                    ocr_backend="fixture-ocr",
                    visual_font_match=True,
                    matched_font_lines=2,
                    geometry_vector=True,
                    compound_geometry=True,
                    geometry_subpaths=3,
                    evenodd_compound_fills=1,
                    precision_ops=["deterministic_geometry"],
                ),
            )
            for case in cases
        }
    )
    recipe = BenchmarkRecipe(
        recipe_id="identity-baseline",
        version="1",
        stages=[
            RecipeStage(
                name="reconstruction",
                implementation="fixture",
                version="1",
            )
        ],
    )

    scorecard = HarnessRunner(registry, store).run_candidates(
        dataset_id,
        BenchmarkTier.SMOKE,
        recipe,
        manifest,
        limit=2,
    )

    assert scorecard.status is HarnessRunStatus.COMPLETE
    assert scorecard.case_count == len(cases)
    assert scorecard.failure_count == 0
    assert scorecard.quality_mean is not None and scorecard.quality_mean > 0.99
    assert scorecard.latency_p50_ms == 1200
    assert scorecard.provider_calls == len(cases)
    assert scorecard.metric_coverage["semantic.object_fidelity"] == 1.0
    assert scorecard.precision_coverage["local_ocr"] == 1.0
    assert scorecard.precision_coverage["visual_font_match"] == 1.0
    assert scorecard.precision_coverage["geometry_vector"] == 1.0
    assert scorecard.precision_coverage["compound_geometry"] == 1.0
    assert scorecard.result_paths
    assert all(Path(path).is_file() for path in scorecard.result_paths)
    assert store.get_scorecard(scorecard.run_id) is not None


def test_runner_marks_missing_candidate_as_partial(tmp_path: Path) -> None:
    registry, dataset_id = _seed_dataset(tmp_path)
    store = HarnessStore(tmp_path / "harness")
    cases = HarnessCaseFactory(registry).build(dataset_id, BenchmarkTier.SMOKE, limit=2)
    assert len(cases) == 2

    manifest = CandidateManifest(
        candidates={
            cases[0].pair_id: CandidateManifestEntry(result_path=cases[0].target_path)
        }
    )
    recipe = BenchmarkRecipe(recipe_id="partial", version="1")

    scorecard = HarnessRunner(registry, store).run_candidates(
        dataset_id,
        BenchmarkTier.SMOKE,
        recipe,
        manifest,
        limit=2,
    )

    assert scorecard.status is HarnessRunStatus.PARTIAL
    assert scorecard.success_count == 1
    assert scorecard.failure_count == 1
    assert scorecard.failure_rate == 0.5


def test_promotion_gate_blocks_serious_cohort_regression() -> None:
    champion = BenchmarkScorecard(
        scorecard_id="champion",
        run_id="run-champion",
        dataset_id="dataset-v1",
        tier=BenchmarkTier.GOLDEN,
        recipe_id="recipe-a",
        recipe_version="1",
        status=HarnessRunStatus.COMPLETE,
        case_count=10,
        success_count=10,
        failure_count=0,
        manual_review_count=0,
        quality_mean=0.90,
        cohorts={
            "logo": CohortScore(
                cohort="logo",
                case_count=5,
                success_count=5,
                quality_mean=0.90,
            )
        },
    )
    challenger = BenchmarkScorecard(
        scorecard_id="challenger",
        run_id="run-challenger",
        dataset_id="dataset-v1",
        tier=BenchmarkTier.GOLDEN,
        recipe_id="recipe-b",
        recipe_version="1",
        status=HarnessRunStatus.COMPLETE,
        case_count=10,
        success_count=10,
        failure_count=0,
        manual_review_count=0,
        quality_mean=0.91,
        cohorts={
            "logo": CohortScore(
                cohort="logo",
                case_count=5,
                success_count=5,
                quality_mean=0.82,
            )
        },
    )

    decision = compare_scorecards(
        champion,
        challenger,
        PromotionPolicy(max_cohort_drop=0.03),
    )

    assert not decision.eligible
    assert decision.overall_delta is not None and decision.overall_delta > 0
    assert decision.cohort_deltas["logo"] < -0.03
    assert any("cohort logo regressed" in reason for reason in decision.reasons)


def test_promotion_gate_requires_golden_by_default() -> None:
    common = dict(
        dataset_id="dataset-v1",
        tier=BenchmarkTier.REGRESSION,
        status=HarnessRunStatus.COMPLETE,
        case_count=2,
        success_count=2,
        failure_count=0,
        manual_review_count=0,
        quality_mean=0.9,
    )
    champion = BenchmarkScorecard(
        scorecard_id="a",
        run_id="a",
        recipe_id="a",
        recipe_version="1",
        **common,
    )
    challenger = BenchmarkScorecard(
        scorecard_id="b",
        run_id="b",
        recipe_id="b",
        recipe_version="1",
        **common,
    )

    assert not compare_scorecards(champion, challenger).eligible
    assert compare_scorecards(
        champion,
        challenger,
        PromotionPolicy(require_golden=False),
    ).eligible


def test_harness_engine_runner_executes_real_pipeline(tmp_path: Path) -> None:
    registry, dataset_id = _seed_dataset(tmp_path, count=10)
    settings = Settings(
        data_root=tmp_path / "runtime",
        local_ocr_enabled=False,
    )
    store = HarnessStore(settings.harness_dir)
    recipe = BenchmarkRecipe(
        recipe_id="real-engine-local",
        version="1",
        stages=[
            RecipeStage(
                name="reconstruction",
                implementation="production-engine",
                version="phase1d",
            )
        ],
    )

    scorecard = HarnessEngineRunner(settings, registry, store).run(
        dataset_id,
        BenchmarkTier.SMOKE,
        recipe,
        quality_mode=QualityMode.QUICK_2D,
        limit=1,
    )

    assert scorecard.case_count == 1
    assert scorecard.success_count == 1
    assert scorecard.status is HarnessRunStatus.COMPLETE
    assert scorecard.latency_p50_ms > 0
    assert scorecard.precision_coverage
    assert scorecard.provenance.execution_kind == "production_engine"
    assert scorecard.provenance.quality_mode is QualityMode.QUICK_2D
    assert scorecard.provenance.recipe_sha256
    assert scorecard.provenance.dataset_manifest_sha256
    assert scorecard.provenance.qc_policy_id
    assert scorecard.provenance.router_policy_id
    result = store.get_results(scorecard.run_id)[0]
    assert result.runtime_qc.semantic_score is not None
    assert result.runtime_qc.technical_score is not None


def test_harness_recipe_metadata_controls_runtime_without_secrets(tmp_path: Path) -> None:
    registry, _ = _seed_dataset(tmp_path, count=10)
    settings = Settings(data_root=tmp_path / "runtime")
    runner = HarnessEngineRunner(
        settings,
        registry,
        HarnessStore(settings.harness_dir),
    )
    recipe = BenchmarkRecipe(
        recipe_id="challenger",
        version="1",
        metadata={
            "provider_recipe_path": "provider.json",
            "qc_policy_path": "qc-policy.json",
            "router_policy_path": "router-policy.json",
            "harness_route_override": "hybrid",
            "local_ocr_enabled": False,
            "visual_font_match_enabled": False,
            "visual_font_match_min_score": 0.81,
            "visual_font_match_min_margin": 0.06,
            "visual_font_match_max_candidates": 48,
            "local_text_repair_enabled": False,
            "local_text_repair_min_confidence": 0.91,
            "tesseract_language": "vie+eng",
        },
    )

    effective = runner._settings_for_recipe(recipe, tmp_path / "recipes")

    assert effective.provider_recipe_path == (
        tmp_path / "recipes" / "provider.json"
    ).resolve()
    assert effective.qc_policy_path == (
        tmp_path / "recipes" / "qc-policy.json"
    ).resolve()
    assert effective.router_policy_path == (
        tmp_path / "recipes" / "router-policy.json"
    ).resolve()
    assert runner._route_override_for_recipe(recipe).value == "hybrid"
    assert effective.local_ocr_enabled is False
    assert effective.visual_font_match_enabled is False
    assert effective.visual_font_match_min_score == 0.81
    assert effective.visual_font_match_min_margin == 0.06
    assert effective.visual_font_match_max_candidates == 48
    assert effective.local_text_repair_enabled is False
    assert effective.local_text_repair_min_confidence == 0.91
    assert effective.tesseract_language == "vie+eng"
    assert effective.remote_provider_token == settings.remote_provider_token


def test_promotion_gate_rejects_non_equivalent_provenance() -> None:
    champion = BenchmarkScorecard(
        scorecard_id="champion-provenance",
        run_id="champion-provenance",
        dataset_id="dataset-v1",
        tier=BenchmarkTier.GOLDEN,
        recipe_id="recipe-a",
        recipe_version="1",
        status=HarnessRunStatus.COMPLETE,
        case_count=2,
        success_count=2,
        failure_count=0,
        manual_review_count=0,
        quality_mean=0.90,
        provenance=RunProvenance(
            quality_mode=QualityMode.PRINT_READY,
            dataset_manifest_sha256="dataset-a",
        ),
    )
    challenger = BenchmarkScorecard(
        scorecard_id="challenger-provenance",
        run_id="challenger-provenance",
        dataset_id="dataset-v1",
        tier=BenchmarkTier.GOLDEN,
        recipe_id="recipe-b",
        recipe_version="1",
        status=HarnessRunStatus.COMPLETE,
        case_count=2,
        success_count=2,
        failure_count=0,
        manual_review_count=0,
        quality_mean=0.92,
        provenance=RunProvenance(
            quality_mode=QualityMode.MAX_FIDELITY,
            dataset_manifest_sha256="dataset-b",
        ),
    )

    decision = compare_scorecards(champion, challenger)

    assert decision.eligible is False
    assert "dataset manifest fingerprint mismatch" in decision.reasons
    assert "quality mode mismatch" in decision.reasons


def test_harness_route_override_records_fallback_without_fake_remote(
    tmp_path: Path,
) -> None:
    registry, dataset_id = _seed_dataset(tmp_path, count=10)
    settings = Settings(
        data_root=tmp_path / "route-runtime",
        local_ocr_enabled=False,
    )
    store = HarnessStore(settings.harness_dir)
    recipe = BenchmarkRecipe(
        recipe_id="forced-hybrid-no-provider",
        version="1",
        metadata={"harness_route_override": "hybrid"},
    )

    scorecard = HarnessEngineRunner(settings, registry, store).run(
        dataset_id,
        BenchmarkTier.SMOKE,
        recipe,
        quality_mode=QualityMode.QUICK_2D,
        limit=1,
    )

    result = store.get_results(scorecard.run_id)[0]
    assert result.route.selected_route.value == "hybrid"
    assert result.route.requested_override.value == "hybrid"
    assert result.route.remote_available is False
    assert result.route.used_remote is False
    assert "harness_route_override" in result.route.reason_codes
    assert "remote_unavailable_experiment_fallback" in result.route.reason_codes
