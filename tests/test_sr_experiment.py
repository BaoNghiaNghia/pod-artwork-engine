from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pod_artwork_engine.dataset_registry import DatasetRegistry
from pod_artwork_engine.harness import HarnessCaseFactory, HarnessStore
from pod_artwork_engine.harness_models import (
    BenchmarkRecipe,
    BenchmarkTier,
    CandidateManifest,
    CandidateManifestEntry,
    HarnessRunStatus,
    QualityMode,
    SRAdapterKind,
    SRAdapterSpec,
    SRBenchmarkLane,
    SRBenchmarkRecommendation,
    SRBenchmarkReport,
    SRCohortReport,
    SRExperimentSpec,
    SRLaneRun,
    SRPolicyRecommendation,
)
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.sr_experiment import (
    SRExperimentRunner,
    build_sr_policy_proposal,
)


def _art(path: Path, color: tuple[int, int, int], offset: int = 0) -> Path:
    image = Image.new("RGBA", (72, 54), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (8 + offset, 7, 54 + offset, 43),
        fill=(*color, 255),
    )
    draw.ellipse((20, 14, 42, 36), outline=(255, 255, 255, 255), width=3)
    image.save(path)
    return path


def _seed_dataset(tmp_path: Path, count: int = 18):
    settings = Settings(data_root=tmp_path / "runtime")
    registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
    historical_source = _art(
        tmp_path / "historical-source.png",
        (30, 60, 90),
    )
    for index in range(count):
        target = _art(
            tmp_path / f"target-{index}.png",
            (
                (40 + index * 31) % 255,
                (80 + index * 47) % 255,
                (120 + index * 59) % 255,
            ),
            offset=index % 4,
        )
        registry.register_pair(
            f"design-{index}",
            [historical_source],
            target,
            metadata={"artwork_type": "logo", "difficulty": "hard"},
        )
    dataset = registry.create_dataset("sr-experiment", seed="phase2k")
    return settings, registry, dataset.dataset_id


def _cohort(
    *,
    dataset_id: str = "dataset-1",
    tier: BenchmarkTier = BenchmarkTier.GOLDEN,
    fingerprint: str = "fingerprint",
    case_count: int = 4,
) -> SRCohortReport:
    return SRCohortReport(
        cohort_id="cohort-1",
        dataset_id=dataset_id,
        tier=tier,
        input_manifest_path="input.json",
        source_manifest_path="source.json",
        native_manifest_path="native.json",
        lanczos_manifest_path="lanczos.json",
        dataset_manifest_sha256=fingerprint,
        scale_factor=2.0,
        case_count=case_count,
        success_count=case_count,
        failure_count=0,
        shared_input_identity=True,
    )


def _matrix(
    recommendation: SRBenchmarkRecommendation,
    *,
    dataset_id: str = "dataset-1",
    tier: BenchmarkTier = BenchmarkTier.GOLDEN,
    fingerprint: str = "fingerprint",
    comparable: int = 4,
    incomplete: int = 0,
    native: int = 0,
    lanczos: int = 0,
    local: int = 0,
    remote: int = 0,
    local_available: bool = True,
    remote_available: bool = False,
) -> SRBenchmarkReport:
    runs = [
        SRLaneRun(
            lane=SRBenchmarkLane.NATIVE,
            available=True,
            status=HarnessRunStatus.COMPLETE,
            case_count=comparable,
            success_count=comparable,
        ),
        SRLaneRun(
            lane=SRBenchmarkLane.LANCZOS,
            available=True,
            status=HarnessRunStatus.COMPLETE,
            case_count=comparable,
            success_count=comparable,
        ),
        SRLaneRun(
            lane=SRBenchmarkLane.LOCAL_SR,
            available=local_available,
            status=(
                HarnessRunStatus.COMPLETE if local_available else None
            ),
            unavailable_reason=(
                None if local_available else "not configured"
            ),
            case_count=comparable if local_available else 0,
            success_count=comparable if local_available else 0,
        ),
        SRLaneRun(
            lane=SRBenchmarkLane.REMOTE_SR,
            available=remote_available,
            status=(
                HarnessRunStatus.COMPLETE if remote_available else None
            ),
            unavailable_reason=(
                None if remote_available else "not configured"
            ),
            case_count=comparable if remote_available else 0,
            success_count=comparable if remote_available else 0,
        ),
    ]
    return SRBenchmarkReport(
        matrix_id="matrix-1",
        dataset_id=dataset_id,
        tier=tier,
        recipe_id="recipe",
        recipe_version="1",
        quality_mode=QualityMode.PRINT_READY,
        min_quality_gain=0.01,
        min_detail_gain=0.03,
        max_semantic_drop=0.02,
        dataset_manifest_sha256=fingerprint,
        runs=runs,
        comparable_case_count=comparable,
        incomplete_count=incomplete,
        native_preferred_count=native,
        lanczos_preferred_count=lanczos,
        local_sr_preferred_count=local,
        remote_sr_preferred_count=remote,
        recommendation=recommendation,
    )


def test_policy_proposes_local_sr_only_with_sufficient_golden_evidence() -> None:
    spec = SRExperimentSpec(
        experiment_id="experiment-1",
        dataset_id="dataset-1",
        pre_sr_manifest_path="pre.json",
        recipe_path="recipe.json",
        min_comparable_cases=3,
        min_decisive_wins=2,
    )
    proposal = build_sr_policy_proposal(
        spec,
        _cohort(),
        _matrix(
            SRBenchmarkRecommendation.LOCAL_SR_FOR_HUMAN_REVIEW,
            comparable=4,
            local=3,
            lanczos=1,
            local_available=True,
        ),
    )

    assert proposal.recommendation is SRPolicyRecommendation.LOCAL_SR
    assert proposal.sufficient_evidence is True
    assert proposal.requires_human_approval is True
    assert proposal.automatically_applied is False
    assert proposal.production_execution_enabled is False


def test_policy_can_keep_lanczos_after_complete_measured_sr_loss() -> None:
    spec = SRExperimentSpec(
        experiment_id="experiment-lanczos",
        dataset_id="dataset-1",
        pre_sr_manifest_path="pre.json",
        recipe_path="recipe.json",
        min_comparable_cases=3,
        min_decisive_wins=2,
    )
    proposal = build_sr_policy_proposal(
        spec,
        _cohort(),
        _matrix(
            SRBenchmarkRecommendation.KEEP_LANCZOS,
            comparable=4,
            lanczos=4,
            local_available=True,
        ),
    )

    assert proposal.recommendation is SRPolicyRecommendation.KEEP_LANCZOS
    assert proposal.sufficient_evidence is True
    assert proposal.local_backend_available is True


def test_policy_falls_back_to_manual_review_when_evidence_is_too_small() -> None:
    spec = SRExperimentSpec(
        experiment_id="experiment-small",
        dataset_id="dataset-1",
        pre_sr_manifest_path="pre.json",
        recipe_path="recipe.json",
        min_comparable_cases=5,
        min_decisive_wins=2,
    )
    proposal = build_sr_policy_proposal(
        spec,
        _cohort(case_count=2),
        _matrix(
            SRBenchmarkRecommendation.LOCAL_SR_FOR_HUMAN_REVIEW,
            comparable=2,
            local=2,
            local_available=True,
        ),
    )

    assert proposal.recommendation is SRPolicyRecommendation.MANUAL_REVIEW
    assert proposal.sufficient_evidence is False
    assert any("comparable Golden cases" in reason for reason in proposal.reasons)


def test_policy_requires_complete_measured_sr_runs() -> None:
    spec = SRExperimentSpec(
        experiment_id="experiment-partial",
        dataset_id="dataset-1",
        pre_sr_manifest_path="pre.json",
        recipe_path="recipe.json",
        min_comparable_cases=3,
        min_decisive_wins=2,
    )
    matrix = _matrix(
        SRBenchmarkRecommendation.KEEP_LANCZOS,
        comparable=4,
        lanczos=4,
        local_available=True,
    )
    local_run = next(
        run for run in matrix.runs if run.lane is SRBenchmarkLane.LOCAL_SR
    )
    local_run.status = HarnessRunStatus.PARTIAL
    local_run.success_count = 2

    proposal = build_sr_policy_proposal(spec, _cohort(), matrix)

    assert proposal.recommendation is SRPolicyRecommendation.MANUAL_REVIEW
    assert proposal.sufficient_evidence is False
    assert any("backend runs are incomplete" in reason for reason in proposal.reasons)


def test_policy_rejects_dataset_fingerprint_mismatch() -> None:
    spec = SRExperimentSpec(
        dataset_id="dataset-1",
        pre_sr_manifest_path="pre.json",
        recipe_path="recipe.json",
    )
    with pytest.raises(ValueError, match="dataset fingerprints"):
        build_sr_policy_proposal(
            spec,
            _cohort(fingerprint="a"),
            _matrix(
                SRBenchmarkRecommendation.KEEP_LANCZOS,
                fingerprint="b",
                lanczos=4,
            ),
        )


def test_experiment_rejects_adapter_scale_mismatch(tmp_path: Path) -> None:
    adapter = SRAdapterSpec(
        adapter_id="wrong-scale",
        kind=SRAdapterKind.LOCAL_COMMAND,
        scale_factor=3.0,
        command=["python", "tool.py", "{input}", "{output}"],
    )
    adapter_path = tmp_path / "adapter.json"
    adapter_path.write_text(adapter.model_dump_json(indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="scale_factor"):
        SRExperimentRunner._validate_adapter(
            adapter_path,
            expected_kind=SRAdapterKind.LOCAL_COMMAND,
            scale_factor=2.0,
        )


def test_experiment_requires_golden_by_default(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "runtime")
    runner = SRExperimentRunner(
        settings,
        object(),
        HarnessStore(settings.harness_dir),
    )
    spec = SRExperimentSpec(
        dataset_id="dataset-1",
        pre_sr_manifest_path="missing.json",
        recipe_path="missing-recipe.json",
        tier=BenchmarkTier.SMOKE,
    )

    with pytest.raises(ValueError, match="Golden Holdout"):
        runner.run(spec, spec_base=tmp_path)


def test_experiment_without_sr_backends_stays_manual_and_persists_artifacts(
    tmp_path: Path,
) -> None:
    settings, registry, dataset_id = _seed_dataset(tmp_path)
    store = HarnessStore(settings.harness_dir)
    golden_cases = HarnessCaseFactory(registry).build(
        dataset_id,
        BenchmarkTier.GOLDEN,
    )
    assert golden_cases

    candidates: dict[str, CandidateManifestEntry] = {}
    for index, case in enumerate(golden_cases):
        pre_sr = _art(
            tmp_path / f"pre-sr-{index}.png",
            (
                (170 + index * 11) % 255,
                (30 + index * 17) % 255,
                (90 + index * 23) % 255,
            ),
            offset=(index + 2) % 4,
        )
        candidates[case.pair_id] = CandidateManifestEntry(
            result_path=str(pre_sr),
        )
    manifest = CandidateManifest(candidates=candidates)
    manifest_path = tmp_path / "pre-sr.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    recipe = BenchmarkRecipe(
        recipe_id="phase2k-fixture",
        version="1",
    )
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(recipe.model_dump_json(indent=2), encoding="utf-8")

    report = SRExperimentRunner(settings, registry, store).run(
        SRExperimentSpec(
            experiment_id="golden-no-backend",
            dataset_id=dataset_id,
            pre_sr_manifest_path=str(manifest_path),
            recipe_path=str(recipe_path),
            tier=BenchmarkTier.GOLDEN,
            min_comparable_cases=1,
            min_decisive_wins=1,
        )
    )

    assert report.policy.recommendation is SRPolicyRecommendation.MANUAL_REVIEW
    assert report.policy.sufficient_evidence is False
    assert report.policy.local_backend_available is False
    assert report.policy.remote_backend_available is False
    assert report.local_adapter_report_path is None
    assert report.remote_adapter_report_path is None
    assert Path(report.cohort_report_path).is_file()
    assert Path(report.matrix_report_path).is_file()
    assert Path(report.policy_proposal_path).is_file()
    assert (store.sr_experiment_dir(report.experiment_id) / "report.json").is_file()
    assert report.production_execution_enabled is False
