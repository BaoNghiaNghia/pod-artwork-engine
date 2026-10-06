from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pod_artwork_engine.contracts import (
    FeatureCorrespondenceDisposition,
    JobState,
    QualityMode,
    MultiReferenceCorrespondenceEvidence,
    ReferenceCorrespondenceEvidence,
)
from pod_artwork_engine.dataset_registry import DatasetRegistry
from pod_artwork_engine.dewarp_benchmark import (
    DewarpBenchmarkError,
    DewarpBenchmarkMatrixRunner,
    DewarpMaterializer,
    _region_cells,
    _warp_reference,
)
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.harness import (
    HarnessCaseFactory,
    HarnessStore,
    load_candidate_manifest,
)
from pod_artwork_engine.harness_models import (
    BenchmarkCaseResult,
    BenchmarkScorecard,
    BenchmarkTier,
    DewarpBenchmarkRecommendation,
    DewarpBenchmarkSpec,
    DewarpMaterializationSpec,
    HarnessRunStatus,
    RegistrationLaneCalibration,
    RegistrationPolicyProposal,
    RegistrationPolicyRecommendation,
    RegistrationThresholds,
    RunProvenance,
)
from pod_artwork_engine.settings import Settings


IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]


def _art(path: Path, *, offset: int = 0) -> Path:
    image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (20 + offset, 26, 100 + offset, 102),
        fill=(40, 120, 220, 255),
    )
    draw.ellipse(
        (42 + offset, 45, 78 + offset, 81),
        fill=(250, 220, 40, 255),
    )
    image.save(path)
    return path


def _dataset(tmp_path: Path) -> tuple[DatasetRegistry, str]:
    registry = DatasetRegistry(
        tmp_path / "engine.sqlite3",
        tmp_path / "datasets",
    )
    primary = _art(tmp_path / "primary.png")
    secondary = _art(tmp_path / "secondary.png", offset=1)
    target = _art(tmp_path / "target.png")
    pair = registry.register_pair(
        "registration-case",
        [primary, secondary],
        target,
    )
    dataset = registry.create_dataset(
        "registration",
        pair_ids=[pair.pair_id],
        train_ratio=0.0,
        validation_ratio=0.0,
        golden_ratio=1.0,
    )
    return registry, dataset.dataset_id


def _fingerprint(registry: DatasetRegistry, dataset_id: str) -> str:
    dataset = registry.get_dataset(dataset_id)
    assert dataset is not None
    return hashlib.sha256(Path(dataset.manifest_path).read_bytes()).hexdigest()


def _policy(
    dataset_id: str,
    run_id: str,
    *,
    recommendation: RegistrationPolicyRecommendation,
    sufficient: bool = True,
) -> RegistrationPolicyProposal:
    thresholds = RegistrationThresholds(
        min_match_count=6,
        min_inlier_count=5,
        min_inlier_ratio=0.60,
        min_spatial_coverage=0.18,
        max_mean_reprojection_error=0.025,
        max_median_reprojection_error=0.018,
        max_homography_error_ratio=0.82,
    )
    affine = RegistrationLaneCalibration(
        lane="affine",
        case_count=3,
        accepted_reference_count=3,
        sample_count=3,
        sufficient_evidence=True,
        thresholds=thresholds.model_copy(
            update={"max_homography_error_ratio": None}
        ),
    )
    homography = RegistrationLaneCalibration(
        lane="homography",
        case_count=3,
        accepted_reference_count=3,
        sample_count=3,
        sufficient_evidence=True,
        thresholds=thresholds,
    )
    return RegistrationPolicyProposal(
        dataset_id=dataset_id,
        source_run_ids=[run_id],
        source_scorecard_ids=["score-source"],
        source_recipe_ids=["local-precision-v20"],
        source_recipe_versions=["20"],
        source_tiers=[BenchmarkTier.GOLDEN],
        affine=affine,
        homography=homography,
        recommendation=recommendation,
        sufficient_evidence=sufficient,
        requires_human_approval=True,
        automatically_applied=False,
        production_execution_enabled=False,
    )


def _evidence(
    *,
    disposition: FeatureCorrespondenceDisposition,
    transform: list[float] | None = None,
    inlier_ratio: float = 0.90,
) -> MultiReferenceCorrespondenceEvidence:
    return MultiReferenceCorrespondenceEvidence(
        reference_count=2,
        primary_index=0,
        measured_indices=[0, 1],
        measured_affine_count=int(
            disposition is FeatureCorrespondenceDisposition.MEASURED_AFFINE
        ),
        measured_homography_count=int(
            disposition is FeatureCorrespondenceDisposition.MEASURED_HOMOGRAPHY
        ),
        mean_inlier_ratio=inlier_ratio,
        mean_reprojection_error=0.005,
        references=[
            ReferenceCorrespondenceEvidence(
                index=0,
                disposition=FeatureCorrespondenceDisposition.IDENTITY,
                model="identity",
                inlier_ratio=1.0,
                spatial_coverage=1.0,
                mean_reprojection_error=0.0,
                median_reprojection_error=0.0,
                transform_matrix=IDENTITY,
            ),
            ReferenceCorrespondenceEvidence(
                index=1,
                disposition=disposition,
                model=(
                    "homography"
                    if disposition
                    is FeatureCorrespondenceDisposition.MEASURED_HOMOGRAPHY
                    else "affine"
                ),
                match_count=10,
                inlier_count=9,
                inlier_ratio=inlier_ratio,
                spatial_coverage=0.70,
                mean_reprojection_error=0.005,
                median_reprojection_error=0.004,
                affine_inlier_ratio=0.85,
                affine_mean_reprojection_error=0.008,
                affine_median_reprojection_error=0.007,
                homography_inlier_ratio=0.90,
                homography_mean_reprojection_error=0.005,
                homography_median_reprojection_error=0.004,
                homography_error_ratio=0.57,
                transform_matrix=transform or IDENTITY,
            ),
        ],
    )


def _source_run(
    registry: DatasetRegistry,
    dataset_id: str,
    store: HarnessStore,
    *,
    run_id: str,
    evidence: MultiReferenceCorrespondenceEvidence,
) -> None:
    cases = HarnessCaseFactory(registry).build(
        dataset_id,
        BenchmarkTier.GOLDEN,
    )
    assert len(cases) == 1
    scorecard = BenchmarkScorecard(
        run_id=run_id,
        dataset_id=dataset_id,
        tier=BenchmarkTier.GOLDEN,
        recipe_id="local-precision-v20",
        recipe_version="20",
        status=HarnessRunStatus.COMPLETE,
        case_count=1,
        success_count=1,
        failure_count=0,
        manual_review_count=0,
        quality_mean=0.90,
        semantic_mean=0.90,
        technical_mean=0.90,
        provenance=RunProvenance(
            dataset_manifest_sha256=_fingerprint(registry, dataset_id)
        ),
    )
    store.save_model(store.scorecard_path(run_id), scorecard)
    case = cases[0]
    store.save_model(
        store.run_dir(run_id) / "results" / f"{case.case_id}.json",
        BenchmarkCaseResult(
            case_id=case.case_id,
            pair_id=case.pair_id,
            artwork_identity=case.artwork_identity,
            success=True,
            quality_score=0.90,
            metadata={
                "feature_correspondence": evidence.model_dump(mode="json")
            },
        ),
    )


def _materialize(
    tmp_path: Path,
    *,
    disposition: FeatureCorrespondenceDisposition,
    recommendation: RegistrationPolicyRecommendation,
    transform: list[float] | None = None,
) -> tuple[Settings, DatasetRegistry, HarnessStore, object]:
    settings = Settings(data_root=tmp_path / "runtime")
    settings.ensure_directories()
    registry, dataset_id = _dataset(tmp_path)
    store = HarnessStore(settings.harness_dir)
    run_id = "run-registration"
    _source_run(
        registry,
        dataset_id,
        store,
        run_id=run_id,
        evidence=_evidence(disposition=disposition, transform=transform),
    )
    policy = _policy(
        dataset_id,
        run_id,
        recommendation=recommendation,
    ).model_copy(
        update={"dataset_manifest_sha256": _fingerprint(registry, dataset_id)}
    )
    policy_path = tmp_path / "registration-policy.json"
    store.save_model(policy_path, policy)
    report = DewarpMaterializer(settings, registry, store).materialize(
        DewarpMaterializationSpec(
            dataset_id=dataset_id,
            source_run_id=run_id,
            registration_policy_path=str(policy_path),
            canonical_size=256,
        )
    )
    return settings, registry, store, report


def test_affine_materialization_is_benchmark_only(tmp_path: Path) -> None:
    _, _, _, report = _materialize(
        tmp_path,
        disposition=FeatureCorrespondenceDisposition.MEASURED_AFFINE,
        recommendation=RegistrationPolicyRecommendation.AFFINE_FOR_DEWARP_BENCHMARK,
    )

    assert report.success_count == 1
    assert report.affine_count == 1
    assert report.homography_count == 0
    assert report.production_execution_enabled is False
    assert report.automatically_applied is False
    assert Path(report.native_manifest_path).is_file()
    assert Path(report.dewarp_manifest_path).is_file()
    case = report.cases[0]
    assert case.success is True
    assert len(case.region_cells) == 16
    assert Path(case.dewarped_path or "").is_file()


def test_homography_materialization_is_supported_in_benchmark_lane(
    tmp_path: Path,
) -> None:
    projective = [
        1.0, 0.02, 0.0,
        -0.01, 1.0, 0.0,
        0.03, -0.02, 1.0,
    ]
    _, _, _, report = _materialize(
        tmp_path,
        disposition=FeatureCorrespondenceDisposition.MEASURED_HOMOGRAPHY,
        recommendation=RegistrationPolicyRecommendation.HOMOGRAPHY_FOR_DEWARP_BENCHMARK,
        transform=projective,
    )

    assert report.success_count == 1
    assert report.homography_count == 1
    assert report.cases[0].model == "homography"
    assert report.production_execution_enabled is False


def test_policy_threshold_rejection_fails_closed(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "runtime")
    settings.ensure_directories()
    registry, dataset_id = _dataset(tmp_path)
    store = HarnessStore(settings.harness_dir)
    run_id = "run-registration"
    _source_run(
        registry,
        dataset_id,
        store,
        run_id=run_id,
        evidence=_evidence(
            disposition=FeatureCorrespondenceDisposition.MEASURED_AFFINE,
            inlier_ratio=0.61,
        ),
    )
    policy = _policy(
        dataset_id,
        run_id,
        recommendation=RegistrationPolicyRecommendation.AFFINE_FOR_DEWARP_BENCHMARK,
    ).model_copy(
        update={"dataset_manifest_sha256": _fingerprint(registry, dataset_id)}
    )
    assert policy.affine.thresholds is not None
    policy.affine.thresholds.min_inlier_ratio = 0.80
    policy_path = tmp_path / "registration-policy.json"
    store.save_model(policy_path, policy)

    report = DewarpMaterializer(settings, registry, store).materialize(
        DewarpMaterializationSpec(
            dataset_id=dataset_id,
            source_run_id=run_id,
            registration_policy_path=str(policy_path),
            canonical_size=256,
        )
    )

    assert report.success_count == 0
    assert report.failure_count == 1
    assert report.cases[0].fail_closed is True
    native_manifest = load_candidate_manifest(Path(report.native_manifest_path))
    native_entry = next(iter(native_manifest.candidates.values()))
    assert Path(native_entry.result_path).is_file()
    assert native_entry.metadata["fail_closed"] is False


def test_insufficient_registration_policy_is_rejected(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "runtime")
    settings.ensure_directories()
    registry, dataset_id = _dataset(tmp_path)
    store = HarnessStore(settings.harness_dir)
    run_id = "run-registration"
    _source_run(
        registry,
        dataset_id,
        store,
        run_id=run_id,
        evidence=_evidence(
            disposition=FeatureCorrespondenceDisposition.MEASURED_AFFINE
        ),
    )
    policy = _policy(
        dataset_id,
        run_id,
        recommendation=RegistrationPolicyRecommendation.INSUFFICIENT_EVIDENCE,
        sufficient=False,
    )
    policy_path = tmp_path / "registration-policy.json"
    store.save_model(policy_path, policy)

    with pytest.raises(DewarpBenchmarkError, match="insufficient"):
        DewarpMaterializer(settings, registry, store).materialize(
            DewarpMaterializationSpec(
                dataset_id=dataset_id,
                source_run_id=run_id,
                registration_policy_path=str(policy_path),
            )
        )


def test_region_sampling_detects_alignment_improvement() -> None:
    primary = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    draw = ImageDraw.Draw(primary)
    draw.rectangle((30, 30, 90, 90), fill=(255, 255, 255, 255))
    shifted = Image.new("RGBA", primary.size, (0, 0, 0, 0))
    shifted.alpha_composite(primary, (10, 0))
    # source -> target translation is -10 pixels in normalized coordinates.
    transform = [
        1.0, 0.0, -10 / 127,
        0.0, 1.0, 0.0,
        0.0, 0.0, 1.0,
    ]
    dewarped = _warp_reference(shifted, transform)

    cells, native_mean, dewarp_mean = _region_cells(
        primary,
        shifted,
        dewarped,
    )

    assert len(cells) == 16
    assert dewarp_mean > native_mean
    assert sum(cell.delta > 0 for cell in cells) > 0


def test_dewarp_matrix_scores_native_and_registered_manifests(
    tmp_path: Path,
) -> None:
    settings, registry, store, cohort = _materialize(
        tmp_path,
        disposition=FeatureCorrespondenceDisposition.MEASURED_AFFINE,
        recommendation=RegistrationPolicyRecommendation.AFFINE_FOR_DEWARP_BENCHMARK,
    )
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(
        """{
  "recipe_id": "dewarp-test-v20",
  "version": "20",
  "stages": []
}
""",
        encoding="utf-8",
    )
    report = DewarpBenchmarkMatrixRunner(settings, registry, store).run(
        DewarpBenchmarkSpec(
            dataset_id=cohort.dataset_id,
            recipe_path=str(recipe_path),
            native_manifest_path=cohort.native_manifest_path,
            dewarp_manifest_path=cohort.dewarp_manifest_path,
            min_comparable_cases=1,
        )
    )

    assert report.comparable_case_count == 1
    assert report.recommendation in {
        DewarpBenchmarkRecommendation.KEEP_NATIVE,
        DewarpBenchmarkRecommendation.DEWARP_FOR_HUMAN_REVIEW,
    }
    assert report.production_execution_enabled is False
    assert report.requires_human_approval is True


def test_production_engine_does_not_execute_dewarp(tmp_path: Path) -> None:
    source = _art(tmp_path / "production-source.png")
    engine = Engine(Settings(data_root=tmp_path / "production-runtime"))
    job = engine.create_job([source], QualityMode.QUICK_2D)

    result = engine.run_job(job.job_id)

    assert result.state in {JobState.COMPLETED, JobState.REVIEW_REQUIRED}
    candidate = engine.checkpoints.payload(job.job_id, "candidate")
    precision_ops = candidate.get("precision_ops") or []
    assert all("dewarp" not in str(item).lower() for item in precision_ops)
    assert engine.checkpoints.payload(job.job_id, "dewarp") is None
