from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from pod_artwork_engine.contracts import SemanticJudgeResult
from pod_artwork_engine.dataset_registry import DatasetRegistry
from pod_artwork_engine.harness import (
    HarnessCaseFactory,
    HarnessRunner,
    HarnessStore,
    compare_scorecards,
)
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
    PromotionPolicy,
    RecipeStage,
)


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
