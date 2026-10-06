from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pod_artwork_engine.contracts import (
    BoundingBox,
    JobState,
    MaterialSeparationDisposition,
    MaterialSeparationEvidence,
    QualityMode,
)
from pod_artwork_engine.dataset_registry import DatasetRegistry
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.harness import HarnessCaseFactory, HarnessStore, load_candidate_manifest
from pod_artwork_engine.harness_models import (
    BenchmarkCaseResult,
    BenchmarkScorecard,
    BenchmarkTier,
    HarnessRunStatus,
    MaterialSeparationBenchmarkRecommendation,
    MaterialSeparationBenchmarkSpec,
    MaterialSeparationExperimentSpec,
    MaterialSeparationMaterializationSpec,
    MaterialSeparationPolicyRecommendation,
    RunProvenance,
)
from pod_artwork_engine.material_separation_benchmark import (
    MaterialSeparationBenchmarkError,
    MaterialSeparationBenchmarkMatrixRunner,
    MaterialSeparationMaterializer,
)
from pod_artwork_engine.material_separation_experiment import (
    MaterialSeparationExperimentError,
    MaterialSeparationExperimentRunner,
    build_material_separation_policy_proposal,
)
from pod_artwork_engine.settings import Settings


def _flat_source(path: Path, color: tuple[int, int, int], *, offset: int = 0) -> Path:
    image = Image.new("RGBA", (128, 128), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (28 + offset, 30, 98 + offset, 100),
        fill=(*color, 255),
    )
    image.save(path)
    return path


def _transparent_target(
    path: Path,
    color: tuple[int, int, int],
    *,
    offset: int = 0,
) -> Path:
    image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (28 + offset, 30, 98 + offset, 100),
        fill=(*color, 255),
    )
    image.save(path)
    return path


def _alpha_source(path: Path) -> Path:
    image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((30, 30, 98, 98), fill=(30, 120, 220, 255))
    image.save(path)
    return path


def _fingerprint(registry: DatasetRegistry, dataset_id: str) -> str:
    dataset = registry.get_dataset(dataset_id)
    assert dataset is not None
    return hashlib.sha256(Path(dataset.manifest_path).read_bytes()).hexdigest()


def _simple_evidence() -> MaterialSeparationEvidence:
    return MaterialSeparationEvidence(
        disposition=MaterialSeparationDisposition.SIMPLE_BORDER_BACKGROUND,
        primary_index=0,
        confidence=0.94,
        fail_closed=False,
        artwork_bbox=BoundingBox(x=0.20, y=0.20, width=0.62, height=0.62),
        source_has_alpha=True,
        meaningful_alpha=False,
        transparent_fraction=0.0,
        visible_fraction=1.0,
        border_uniformity=0.99,
        edge_contact_ratio=0.0,
        foreground_contrast=0.55,
        reason_codes=["uniform_border_background_candidate"],
    )


def _alpha_evidence() -> MaterialSeparationEvidence:
    return MaterialSeparationEvidence(
        disposition=MaterialSeparationDisposition.EXISTING_ALPHA,
        primary_index=0,
        confidence=0.97,
        fail_closed=False,
        artwork_bbox=BoundingBox(x=0.20, y=0.20, width=0.62, height=0.62),
        source_has_alpha=True,
        meaningful_alpha=True,
        transparent_fraction=0.60,
        visible_fraction=0.40,
        border_uniformity=0.98,
        edge_contact_ratio=0.0,
        foreground_contrast=0.45,
        reason_codes=["meaningful_source_alpha"],
    )


def _semantic_evidence() -> MaterialSeparationEvidence:
    return MaterialSeparationEvidence(
        disposition=MaterialSeparationDisposition.SEMANTIC_REQUIRED,
        primary_index=0,
        confidence=0.88,
        fail_closed=True,
        artwork_bbox=BoundingBox(x=0.0, y=0.0, width=1.0, height=1.0),
        source_has_alpha=False,
        meaningful_alpha=False,
        border_uniformity=0.40,
        edge_contact_ratio=1.0,
        foreground_contrast=0.30,
        reason_codes=["artwork_touches_source_edge"],
        missing_capabilities=["need_material_separation", "need_semantic_reconstruction"],
    )


def _golden_dataset(
    tmp_path: Path,
    *,
    count: int = 3,
) -> tuple[DatasetRegistry, str]:
    registry = DatasetRegistry(tmp_path / "engine.sqlite3", tmp_path / "datasets")
    colors = [(220, 40, 40), (40, 160, 70), (40, 90, 220), (210, 120, 30)]
    pair_ids = []
    for index in range(count):
        source = _flat_source(
            tmp_path / f"source-{index}.png",
            colors[index],
            offset=index,
        )
        target = _transparent_target(
            tmp_path / f"target-{index}.png",
            colors[index],
            offset=index,
        )
        pair = registry.register_pair(f"material-{index}", [source], target)
        pair_ids.append(pair.pair_id)
    dataset = registry.create_dataset(
        "material-separation",
        pair_ids=pair_ids,
        train_ratio=0.0,
        validation_ratio=0.0,
        golden_ratio=1.0,
    )
    return registry, dataset.dataset_id


def _source_run(
    registry: DatasetRegistry,
    dataset_id: str,
    store: HarnessStore,
    *,
    evidence: MaterialSeparationEvidence | None = None,
    run_id: str = "run-material-source",
) -> None:
    cases = HarnessCaseFactory(registry).build(dataset_id, BenchmarkTier.GOLDEN)
    scorecard = BenchmarkScorecard(
        run_id=run_id,
        dataset_id=dataset_id,
        tier=BenchmarkTier.GOLDEN,
        recipe_id="local-precision-v22",
        recipe_version="22",
        status=HarnessRunStatus.COMPLETE,
        case_count=len(cases),
        success_count=len(cases),
        failure_count=0,
        manual_review_count=0,
        quality_mean=0.85,
        semantic_mean=0.85,
        technical_mean=0.85,
        provenance=RunProvenance(
            dataset_manifest_sha256=_fingerprint(registry, dataset_id)
        ),
    )
    store.save_model(store.scorecard_path(run_id), scorecard)
    for case in cases:
        payload = evidence or _simple_evidence()
        store.save_model(
            store.run_dir(run_id) / "results" / f"{case.case_id}.json",
            BenchmarkCaseResult(
                case_id=case.case_id,
                pair_id=case.pair_id,
                artwork_identity=case.artwork_identity,
                success=True,
                quality_score=0.85,
                metadata={"material_separation": payload.model_dump(mode="json")},
            ),
        )


def _recipe(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "recipe_id": "material-separation-v22",
                "version": "22",
                "stages": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_simple_background_materialization_removes_border(
    tmp_path: Path,
) -> None:
    settings = Settings(data_root=tmp_path / "runtime")
    settings.ensure_directories()
    registry, dataset_id = _golden_dataset(tmp_path, count=1)
    store = HarnessStore(settings.harness_dir)
    _source_run(registry, dataset_id, store)

    report = MaterialSeparationMaterializer(
        settings,
        registry,
        store,
    ).materialize(
        MaterialSeparationMaterializationSpec(
            dataset_id=dataset_id,
            source_run_id="run-material-source",
            min_evidence_confidence=0.60,
        )
    )

    assert report.success_count == 1
    assert report.simple_border_count == 1
    case = report.cases[0]
    assert case.success is True
    assert case.separated_transparent_fraction > case.native_transparent_fraction
    assert Path(case.native_path or "").is_file()
    assert Path(case.separated_path or "").is_file()
    assert report.production_execution_enabled is False


def test_existing_alpha_is_preserved_without_reconstruction(
    tmp_path: Path,
) -> None:
    settings = Settings(data_root=tmp_path / "runtime-alpha")
    settings.ensure_directories()
    registry = DatasetRegistry(tmp_path / "alpha.sqlite3", tmp_path / "alpha-datasets")
    source = _alpha_source(tmp_path / "alpha-source.png")
    target = _alpha_source(tmp_path / "alpha-target.png")
    pair = registry.register_pair("alpha", [source], target)
    dataset = registry.create_dataset(
        "alpha-material",
        pair_ids=[pair.pair_id],
        train_ratio=0.0,
        validation_ratio=0.0,
        golden_ratio=1.0,
    )
    store = HarnessStore(settings.harness_dir)
    _source_run(
        registry,
        dataset.dataset_id,
        store,
        evidence=_alpha_evidence(),
    )

    report = MaterialSeparationMaterializer(
        settings,
        registry,
        store,
    ).materialize(
        MaterialSeparationMaterializationSpec(
            dataset_id=dataset.dataset_id,
            source_run_id="run-material-source",
        )
    )

    assert report.existing_alpha_count == 1
    assert report.success_count == 1
    case = report.cases[0]
    assert case.native_transparent_fraction == pytest.approx(
        case.separated_transparent_fraction
    )


def test_semantic_required_fails_closed_but_keeps_native_control(
    tmp_path: Path,
) -> None:
    settings = Settings(data_root=tmp_path / "runtime-semantic")
    settings.ensure_directories()
    registry, dataset_id = _golden_dataset(tmp_path, count=1)
    store = HarnessStore(settings.harness_dir)
    _source_run(
        registry,
        dataset_id,
        store,
        evidence=_semantic_evidence(),
    )

    report = MaterialSeparationMaterializer(
        settings,
        registry,
        store,
    ).materialize(
        MaterialSeparationMaterializationSpec(
            dataset_id=dataset_id,
            source_run_id="run-material-source",
        )
    )

    assert report.success_count == 0
    assert report.fail_closed_count == 1
    assert report.cases[0].fail_closed is True
    native = load_candidate_manifest(Path(report.native_manifest_path))
    native_entry = next(iter(native.candidates.values()))
    assert Path(native_entry.result_path).is_file()
    separated = load_candidate_manifest(Path(report.separated_manifest_path))
    separated_entry = next(iter(separated.candidates.values()))
    assert separated_entry.metadata["fail_closed"] is True


def test_material_separation_matrix_measures_real_alpha_gain(
    tmp_path: Path,
) -> None:
    settings = Settings(data_root=tmp_path / "runtime-matrix")
    settings.ensure_directories()
    registry, dataset_id = _golden_dataset(tmp_path, count=3)
    store = HarnessStore(settings.harness_dir)
    _source_run(registry, dataset_id, store)
    cohort = MaterialSeparationMaterializer(
        settings,
        registry,
        store,
    ).materialize(
        MaterialSeparationMaterializationSpec(
            dataset_id=dataset_id,
            source_run_id="run-material-source",
        )
    )
    recipe = _recipe(tmp_path / "recipe.json")

    report = MaterialSeparationBenchmarkMatrixRunner(
        settings,
        registry,
        store,
    ).run(
        MaterialSeparationBenchmarkSpec(
            dataset_id=dataset_id,
            recipe_path=str(recipe),
            native_manifest_path=cohort.native_manifest_path,
            separated_manifest_path=cohort.separated_manifest_path,
            min_comparable_cases=3,
            min_quality_gain=0.0,
            min_alpha_gain=0.0,
            min_technical_gain=0.0,
            max_semantic_drop=1.0,
            max_small_detail_drop=1.0,
            max_halo_drop=1.0,
        )
    )

    assert report.comparable_case_count == 3
    assert report.alpha_delta is not None
    assert report.recommendation in {
        MaterialSeparationBenchmarkRecommendation.KEEP_NATIVE,
        MaterialSeparationBenchmarkRecommendation.MATERIAL_SEPARATION_FOR_HUMAN_REVIEW,
    }
    assert report.production_execution_enabled is False


def test_non_golden_materialization_is_rejected(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "runtime-nongolden")
    settings.ensure_directories()
    registry, dataset_id = _golden_dataset(tmp_path, count=1)
    store = HarnessStore(settings.harness_dir)

    with pytest.raises(MaterialSeparationBenchmarkError, match="Golden Holdout"):
        MaterialSeparationMaterializer(
            settings,
            registry,
            store,
        ).materialize(
            MaterialSeparationMaterializationSpec(
                dataset_id=dataset_id,
                source_run_id="missing",
                tier=BenchmarkTier.REGRESSION,
            )
        )


def test_experiment_persists_policy_and_remains_disabled(
    tmp_path: Path,
) -> None:
    settings = Settings(data_root=tmp_path / "runtime-experiment")
    settings.ensure_directories()
    registry, dataset_id = _golden_dataset(tmp_path, count=3)
    store = HarnessStore(settings.harness_dir)
    _source_run(registry, dataset_id, store)
    recipe = _recipe(tmp_path / "experiment-recipe.json")

    report = MaterialSeparationExperimentRunner(
        settings,
        registry,
        store,
    ).run(
        MaterialSeparationExperimentSpec(
            experiment_id="material-experiment-test",
            dataset_id=dataset_id,
            source_run_id="run-material-source",
            recipe_path=str(recipe),
            min_simple_cases=3,
            min_comparable_cases=3,
            min_quality_gain=0.0,
            min_alpha_gain=0.0,
            min_technical_gain=0.0,
            max_semantic_drop=1.0,
            max_small_detail_drop=1.0,
            max_halo_drop=1.0,
            max_materialization_failure_rate=0.0,
        )
    )

    experiment_dir = store.material_separation_experiment_dir(
        "material-experiment-test"
    )
    assert (experiment_dir / "spec.json").is_file()
    assert (experiment_dir / "report.json").is_file()
    assert (experiment_dir / "policy-proposal.json").is_file()
    assert report.policy.production_execution_enabled is False
    assert report.policy.requires_human_approval is True
    assert report.policy.recommendation in {
        MaterialSeparationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL,
        MaterialSeparationPolicyRecommendation.KEEP_DISABLED,
    }


def test_policy_builder_detects_safety_regression() -> None:
    from pod_artwork_engine.harness_models import (
        MaterialSeparationBenchmarkReport,
        MaterialSeparationMaterializationReport,
    )

    spec = MaterialSeparationExperimentSpec(
        experiment_id="policy-safety",
        dataset_id="dataset",
        source_run_id="run",
        recipe_path="recipe.json",
        min_simple_cases=1,
        min_comparable_cases=1,
        max_materialization_failure_rate=0.5,
    )
    cohort = MaterialSeparationMaterializationReport(
        cohort_id="cohort",
        dataset_id="dataset",
        source_run_id="run",
        tier=BenchmarkTier.GOLDEN,
        dataset_manifest_sha256="a" * 64,
        native_manifest_path="native.json",
        separated_manifest_path="separated.json",
        case_count=2,
        success_count=2,
        failure_count=0,
        simple_border_count=2,
    )
    matrix = MaterialSeparationBenchmarkReport(
        matrix_id="matrix",
        dataset_id="dataset",
        tier=BenchmarkTier.GOLDEN,
        recipe_id="v22",
        recipe_version="22",
        dataset_manifest_sha256="a" * 64,
        native_run_id="native",
        separated_run_id="separated",
        native_scorecard_id="score-native",
        separated_scorecard_id="score-separated",
        comparable_case_count=2,
        quality_delta=0.02,
        semantic_delta=-0.20,
        technical_delta=0.01,
        alpha_delta=0.20,
        halo_delta=0.0,
        small_detail_delta=0.0,
        failure_rate_delta=0.0,
        manual_review_rate_delta=0.0,
        recommendation=(
            MaterialSeparationBenchmarkRecommendation.MATERIAL_SEPARATION_FOR_HUMAN_REVIEW
        ),
        sufficient_evidence=True,
    )

    proposal = build_material_separation_policy_proposal(
        spec,
        dataset_manifest_sha256="a" * 64,
        cohort=cohort,
        matrix=matrix,
    )

    assert (
        proposal.recommendation
        is MaterialSeparationPolicyRecommendation.MANUAL_REVIEW
    )
    assert proposal.sufficient_evidence is False
    assert "material_separation_semantic_regression" in proposal.reasons


def test_policy_builder_keeps_disabled_when_gain_is_too_small() -> None:
    from pod_artwork_engine.harness_models import (
        MaterialSeparationBenchmarkReport,
        MaterialSeparationMaterializationReport,
    )

    spec = MaterialSeparationExperimentSpec(
        experiment_id="policy-keep-disabled",
        dataset_id="dataset",
        source_run_id="run",
        recipe_path="recipe.json",
        min_simple_cases=1,
        min_comparable_cases=1,
        min_quality_gain=0.05,
        min_alpha_gain=0.10,
        max_materialization_failure_rate=0.5,
    )
    cohort = MaterialSeparationMaterializationReport(
        cohort_id="cohort",
        dataset_id="dataset",
        source_run_id="run",
        tier=BenchmarkTier.GOLDEN,
        dataset_manifest_sha256="a" * 64,
        native_manifest_path="native.json",
        separated_manifest_path="separated.json",
        case_count=2,
        success_count=2,
        failure_count=0,
        simple_border_count=2,
    )
    matrix = MaterialSeparationBenchmarkReport(
        matrix_id="matrix",
        dataset_id="dataset",
        tier=BenchmarkTier.GOLDEN,
        recipe_id="v22",
        recipe_version="22",
        dataset_manifest_sha256="a" * 64,
        native_run_id="native",
        separated_run_id="separated",
        native_scorecard_id="score-native",
        separated_scorecard_id="score-separated",
        comparable_case_count=2,
        quality_delta=0.01,
        semantic_delta=0.0,
        technical_delta=0.0,
        alpha_delta=0.02,
        halo_delta=0.0,
        small_detail_delta=0.0,
        recommendation=MaterialSeparationBenchmarkRecommendation.KEEP_NATIVE,
        sufficient_evidence=True,
    )

    proposal = build_material_separation_policy_proposal(
        spec,
        dataset_manifest_sha256="a" * 64,
        cohort=cohort,
        matrix=matrix,
    )

    assert (
        proposal.recommendation
        is MaterialSeparationPolicyRecommendation.KEEP_DISABLED
    )
    assert proposal.sufficient_evidence is True
    assert proposal.production_execution_enabled is False


def test_policy_builder_marks_missing_simple_cases_insufficient() -> None:
    from pod_artwork_engine.harness_models import (
        MaterialSeparationBenchmarkReport,
        MaterialSeparationMaterializationReport,
    )

    spec = MaterialSeparationExperimentSpec(
        experiment_id="policy-insufficient",
        dataset_id="dataset",
        source_run_id="run",
        recipe_path="recipe.json",
        min_simple_cases=3,
        min_comparable_cases=1,
        max_materialization_failure_rate=0.5,
    )
    cohort = MaterialSeparationMaterializationReport(
        cohort_id="cohort",
        dataset_id="dataset",
        source_run_id="run",
        tier=BenchmarkTier.GOLDEN,
        dataset_manifest_sha256="a" * 64,
        native_manifest_path="native.json",
        separated_manifest_path="separated.json",
        case_count=2,
        success_count=2,
        failure_count=0,
        existing_alpha_count=2,
        simple_border_count=0,
    )
    matrix = MaterialSeparationBenchmarkReport(
        matrix_id="matrix",
        dataset_id="dataset",
        tier=BenchmarkTier.GOLDEN,
        recipe_id="v22",
        recipe_version="22",
        dataset_manifest_sha256="a" * 64,
        native_run_id="native",
        separated_run_id="separated",
        native_scorecard_id="score-native",
        separated_scorecard_id="score-separated",
        comparable_case_count=2,
        quality_delta=0.0,
        semantic_delta=0.0,
        technical_delta=0.0,
        alpha_delta=0.0,
        halo_delta=0.0,
        small_detail_delta=0.0,
        recommendation=MaterialSeparationBenchmarkRecommendation.KEEP_NATIVE,
        sufficient_evidence=True,
    )

    proposal = build_material_separation_policy_proposal(
        spec,
        dataset_manifest_sha256="a" * 64,
        cohort=cohort,
        matrix=matrix,
    )

    assert (
        proposal.recommendation
        is MaterialSeparationPolicyRecommendation.INSUFFICIENT_EVIDENCE
    )
    assert proposal.sufficient_evidence is False
    assert "simple_border_golden_cases_below_minimum" in proposal.reasons


def test_policy_builder_rejects_non_golden_spec() -> None:
    spec = MaterialSeparationExperimentSpec(
        dataset_id="dataset",
        source_run_id="run",
        recipe_path="recipe.json",
        tier=BenchmarkTier.REGRESSION,
    )
    with pytest.raises(MaterialSeparationExperimentError, match="Golden Holdout"):
        build_material_separation_policy_proposal(
            spec,
            dataset_manifest_sha256="a" * 64,
        )


def test_policy_builder_rejects_fingerprint_mismatch() -> None:
    from pod_artwork_engine.harness_models import MaterialSeparationMaterializationReport

    spec = MaterialSeparationExperimentSpec(
        dataset_id="dataset",
        source_run_id="run",
        recipe_path="recipe.json",
    )
    cohort = MaterialSeparationMaterializationReport(
        cohort_id="cohort",
        dataset_id="dataset",
        source_run_id="run",
        tier=BenchmarkTier.GOLDEN,
        dataset_manifest_sha256="b" * 64,
        native_manifest_path="native.json",
        separated_manifest_path="separated.json",
    )
    with pytest.raises(MaterialSeparationExperimentError, match="fingerprint"):
        build_material_separation_policy_proposal(
            spec,
            dataset_manifest_sha256="a" * 64,
            cohort=cohort,
        )


def test_production_engine_still_does_not_execute_material_separation(
    tmp_path: Path,
) -> None:
    source = _alpha_source(tmp_path / "production-alpha.png")
    engine = Engine(Settings(data_root=tmp_path / "production-runtime"))
    job = engine.create_job([source], QualityMode.QUICK_2D)

    result = engine.run_job(job.job_id)

    assert result.state in {JobState.COMPLETED, JobState.REVIEW_REQUIRED}
    candidate = engine.checkpoints.payload(job.job_id, "candidate")
    precision_ops = candidate.get("precision_ops") or []
    assert "material_separation" not in precision_ops
    evidence = engine.checkpoints.payload(job.job_id, "material_separation")
    assert evidence["method"] == "material_separation_evidence_v1"


def test_cli_exposes_material_separation_commands() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "pod_artwork_engine", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "harness-material-separation-materialize" in completed.stdout
    assert "harness-material-separation-matrix" in completed.stdout
    assert "harness-material-separation-experiment" in completed.stdout
