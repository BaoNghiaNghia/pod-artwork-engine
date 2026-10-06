from __future__ import annotations

import hashlib
import random
from pathlib import Path

from PIL import Image

from pod_artwork_engine import feature_correspondence as correspondence_module
from pod_artwork_engine.contracts import (
    BoundingBox,
    FeatureCorrespondenceDisposition,
    FeatureMatchEvidence,
    JobState,
    MultiReferenceAlignmentEvidence,
    MultiReferenceFusionEvidence,
    NormalizedPoint,
    PreflightResult,
    QualityMode,
    ReferenceAlignmentDisposition,
    ReferenceAlignmentEvidence,
    ReferenceEvidence,
)
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.feature_correspondence import (
    build_feature_correspondence_evidence,
)
from pod_artwork_engine.settings import Settings


FULL_BBOX = BoundingBox(x=0, y=0, width=1, height=1)


def _texture(path: Path, *, seed: int = 7) -> Path:
    rng = random.Random(seed)
    image = Image.new("L", (96, 96))
    image.putdata([rng.randrange(0, 256) for _ in range(96 * 96)])
    image.convert("RGB").save(path)
    return path


def _shift(source: Path, target: Path, *, dx: int, dy: int) -> Path:
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    shifted = Image.new("RGB", image.size, (0, 0, 0))
    shifted.paste(image, (dx, dy))
    shifted.save(target)
    return target


def _flat(path: Path) -> Path:
    Image.new("RGB", (96, 96), (128, 128, 128)).save(path)
    return path


def _preflight(
    path: Path,
    *,
    bbox: BoundingBox = FULL_BBOX,
    confidence: float = 0.95,
) -> PreflightResult:
    with Image.open(path) as image:
        width, height = image.size
    return PreflightResult(
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        filename=path.name,
        width=width,
        height=height,
        image_mode="RGB",
        image_format="PNG",
        file_size_bytes=path.stat().st_size,
        source_quality=0.95,
        artwork_bbox=bbox,
        artwork_confidence=confidence,
    )


def _fusion(
    preflights: list[PreflightResult],
    *,
    secondary_status: str = "consistent",
) -> MultiReferenceFusionEvidence:
    conflict = secondary_status == "conflicting"
    return MultiReferenceFusionEvidence(
        reference_count=len(preflights),
        primary_index=0,
        consensus_confidence=0.90,
        conflict_detected=conflict,
        consistent_indices=(
            [0, 1]
            if len(preflights) > 1 and secondary_status == "consistent"
            else [0]
        ),
        conflicting_indices=[1] if conflict else [],
        ambiguous_indices=(
            [1]
            if len(preflights) > 1 and secondary_status == "ambiguous"
            else []
        ),
        references=[
            ReferenceEvidence(
                index=0,
                sha256=preflights[0].sha256,
                quality_score=0.95,
                similarity_to_primary=1.0,
                status="primary",
            ),
            *(
                [
                    ReferenceEvidence(
                        index=1,
                        sha256=preflights[1].sha256,
                        quality_score=0.95,
                        similarity_to_primary=0.90,
                        status=secondary_status,
                    )
                ]
                if len(preflights) > 1
                else []
            ),
        ],
    )


def _alignment(
    count: int,
    *,
    secondary_disposition: ReferenceAlignmentDisposition = (
        ReferenceAlignmentDisposition.AFFINE_CANDIDATE
    ),
) -> MultiReferenceAlignmentEvidence:
    references = [
        ReferenceAlignmentEvidence(
            index=0,
            disposition=ReferenceAlignmentDisposition.IDENTITY,
            source_bbox=FULL_BBOX,
            target_bbox=FULL_BBOX,
            transform_matrix=[
                1.0, 0.0, 0.0,
                0.0, 1.0, 0.0,
                0.0, 0.0, 1.0,
            ],
            geometry_confidence=0.95,
            aspect_compatibility=1.0,
        )
    ]
    if count > 1:
        references.append(
            ReferenceAlignmentEvidence(
                index=1,
                disposition=secondary_disposition,
                source_bbox=FULL_BBOX,
                target_bbox=FULL_BBOX,
                geometry_confidence=0.90,
                aspect_compatibility=1.0,
                fail_closed=(
                    secondary_disposition
                    in {
                        ReferenceAlignmentDisposition.HOMOGRAPHY_CANDIDATE,
                        ReferenceAlignmentDisposition.SEMANTIC_REQUIRED,
                        ReferenceAlignmentDisposition.MANUAL_REVIEW,
                    }
                ),
            )
        )
    return MultiReferenceAlignmentEvidence(
        reference_count=count,
        primary_index=0,
        aligned_indices=[0, 1] if count > 1 else [0],
        references=references,
    )


def test_texture_rich_shift_produces_measured_affine_correspondence(
    tmp_path: Path,
) -> None:
    primary = _texture(tmp_path / "primary.png")
    secondary = _shift(primary, tmp_path / "secondary.png", dx=4, dy=2)
    preflights = [_preflight(primary), _preflight(secondary)]

    evidence = build_feature_correspondence_evidence(
        [primary, secondary],
        preflights,
        _fusion(preflights),
        _alignment(2),
    )

    measured = evidence.references[1]
    assert (
        measured.disposition
        is FeatureCorrespondenceDisposition.MEASURED_AFFINE
    )
    assert measured.match_count >= 6
    assert measured.inlier_count >= 5
    assert measured.inlier_ratio >= 0.60
    assert measured.median_reprojection_error is not None
    assert measured.median_reprojection_error <= 0.018
    assert len(measured.transform_matrix) == 9
    assert measured.execution_enabled is False
    assert evidence.measured_affine_count == 1


def _projective_matches() -> list[FeatureMatchEvidence]:
    matrix = [
        1.0, 0.03, 0.015,
        -0.02, 1.0, 0.01,
        0.12, -0.08, 1.0,
    ]
    points = [
        (0.18, 0.18),
        (0.40, 0.18),
        (0.62, 0.18),
        (0.80, 0.32),
        (0.22, 0.48),
        (0.48, 0.50),
        (0.72, 0.55),
        (0.25, 0.78),
        (0.58, 0.80),
        (0.82, 0.76),
    ]
    matches: list[FeatureMatchEvidence] = []
    for x, y in points:
        denominator = matrix[6] * x + matrix[7] * y + matrix[8]
        tx = (matrix[0] * x + matrix[1] * y + matrix[2]) / denominator
        ty = (matrix[3] * x + matrix[4] * y + matrix[5]) / denominator
        matches.append(
            FeatureMatchEvidence(
                source=NormalizedPoint(x=x, y=y),
                target=NormalizedPoint(x=tx, y=ty),
                patch_error=0.01,
                uniqueness_margin=0.10,
            )
        )
    return matches


def test_projective_correspondence_can_be_measured_without_enabling_warp(
    monkeypatch,
    tmp_path: Path,
) -> None:
    primary = _texture(tmp_path / "primary.png")
    secondary = _texture(tmp_path / "secondary.png", seed=8)
    preflights = [_preflight(primary), _preflight(secondary)]
    monkeypatch.setattr(
        correspondence_module,
        "measure_feature_matches",
        lambda *args, **kwargs: _projective_matches(),
    )

    evidence = build_feature_correspondence_evidence(
        [primary, secondary],
        preflights,
        _fusion(preflights),
        _alignment(
            2,
            secondary_disposition=ReferenceAlignmentDisposition.HOMOGRAPHY_CANDIDATE,
        ),
    )

    measured = evidence.references[1]
    assert (
        measured.disposition
        is FeatureCorrespondenceDisposition.MEASURED_HOMOGRAPHY
    )
    assert measured.inlier_count >= 6
    assert measured.inlier_ratio >= 0.65
    assert measured.mean_reprojection_error is not None
    assert measured.mean_reprojection_error <= 0.023
    assert len(measured.transform_matrix) == 9
    assert measured.execution_enabled is False
    assert "production_warp_disabled" in measured.reason_codes


def test_flat_reference_fails_closed_when_features_are_insufficient(
    tmp_path: Path,
) -> None:
    primary = _flat(tmp_path / "primary.png")
    secondary = _flat(tmp_path / "secondary.png")
    preflights = [_preflight(primary), _preflight(secondary)]

    evidence = build_feature_correspondence_evidence(
        [primary, secondary],
        preflights,
        _fusion(preflights),
        _alignment(2),
    )

    item = evidence.references[1]
    assert (
        item.disposition
        is FeatureCorrespondenceDisposition.INSUFFICIENT_FEATURES
    )
    assert item.match_count == 0
    assert item.fail_closed is True
    assert evidence.insufficient_feature_count == 1


def test_conflicting_reference_is_excluded_before_patch_matching(
    monkeypatch,
    tmp_path: Path,
) -> None:
    primary = _texture(tmp_path / "primary.png")
    secondary = _texture(tmp_path / "secondary.png", seed=8)
    preflights = [_preflight(primary), _preflight(secondary)]

    def should_not_run(*args, **kwargs):
        raise AssertionError("conflicting reference should not be matched")

    monkeypatch.setattr(
        correspondence_module,
        "measure_feature_matches",
        should_not_run,
    )
    alignment = _alignment(
        2,
        secondary_disposition=ReferenceAlignmentDisposition.MANUAL_REVIEW,
    )

    evidence = build_feature_correspondence_evidence(
        [primary, secondary],
        preflights,
        _fusion(preflights, secondary_status="conflicting"),
        alignment,
    )

    item = evidence.references[1]
    assert item.disposition is FeatureCorrespondenceDisposition.MANUAL_REVIEW
    assert item.fail_closed is True
    assert evidence.excluded_indices == [1]


def _inconsistent_matches() -> list[FeatureMatchEvidence]:
    source_points = [
        (0.15, 0.15),
        (0.35, 0.15),
        (0.55, 0.15),
        (0.75, 0.15),
        (0.20, 0.45),
        (0.42, 0.48),
        (0.65, 0.52),
        (0.80, 0.78),
    ]
    target_points = [
        (0.82, 0.18),
        (0.12, 0.82),
        (0.65, 0.75),
        (0.30, 0.38),
        (0.88, 0.62),
        (0.18, 0.28),
        (0.48, 0.88),
        (0.55, 0.12),
    ]
    return [
        FeatureMatchEvidence(
            source=NormalizedPoint(x=sx, y=sy),
            target=NormalizedPoint(x=tx, y=ty),
            patch_error=0.01,
            uniqueness_margin=0.10,
        )
        for (sx, sy), (tx, ty) in zip(
            source_points,
            target_points,
            strict=True,
        )
    ]


def test_high_reprojection_error_fails_closed_to_manual_review(
    monkeypatch,
    tmp_path: Path,
) -> None:
    primary = _texture(tmp_path / "primary.png")
    secondary = _texture(tmp_path / "secondary.png", seed=8)
    preflights = [_preflight(primary), _preflight(secondary)]
    monkeypatch.setattr(
        correspondence_module,
        "measure_feature_matches",
        lambda *args, **kwargs: _inconsistent_matches(),
    )

    evidence = build_feature_correspondence_evidence(
        [primary, secondary],
        preflights,
        _fusion(preflights),
        _alignment(2),
    )

    item = evidence.references[1]
    assert item.disposition is FeatureCorrespondenceDisposition.MANUAL_REVIEW
    assert item.fail_closed is True
    assert "reprojection_gate_failed" in item.reason_codes


def test_engine_persists_feature_correspondence_without_pixel_operation(
    tmp_path: Path,
) -> None:
    primary = _texture(tmp_path / "primary.png")
    secondary = tmp_path / "secondary.png"
    secondary.write_bytes(primary.read_bytes())
    engine = Engine(Settings(data_root=tmp_path / "runtime"))
    job = engine.create_job([primary, secondary], QualityMode.QUICK_2D)

    result = engine.run_job(job.job_id)

    assert result.state in {JobState.COMPLETED, JobState.REVIEW_REQUIRED}
    evidence = engine.checkpoints.payload(job.job_id, "feature_correspondence")
    assert evidence["method"] == "feature_correspondence_benchmark_v1"
    assert evidence["execution_enabled"] is False
    assert len(evidence["references"]) == 2

    candidate = engine.checkpoints.payload(job.job_id, "candidate")
    assert "feature_correspondence" not in (candidate.get("precision_ops") or [])

    manifest_path = (
        engine.settings.jobs_dir
        / job.job_id
        / "master"
        / "artifact_manifest.json"
    )
    assert manifest_path.is_file()
    assert '"feature_correspondence"' in manifest_path.read_text(encoding="utf-8")
