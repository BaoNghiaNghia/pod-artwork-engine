from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw

from pod_artwork_engine.contracts import (
    BoundingBox,
    JobState,
    MultiReferenceFusionEvidence,
    PreflightResult,
    QualityMode,
    ReferenceAlignmentDisposition,
    ReferenceEvidence,
)
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.reference_alignment import build_reference_alignment
from pod_artwork_engine.settings import Settings


def _preflight(
    *,
    index: int,
    size: tuple[int, int],
    bbox: BoundingBox | None,
    confidence: float = 0.90,
) -> PreflightResult:
    width, height = size
    return PreflightResult(
        sha256=f"{index + 1:064x}",
        filename=f"ref-{index}.png",
        width=width,
        height=height,
        image_mode="RGB",
        image_format="PNG",
        file_size_bytes=1000,
        source_quality=0.90,
        artwork_bbox=bbox,
        artwork_confidence=confidence,
    )


def _fusion(
    *,
    statuses: list[str],
    similarities: list[float] | None = None,
    primary_index: int = 0,
) -> MultiReferenceFusionEvidence:
    similarities = similarities or [1.0] + [0.90] * (len(statuses) - 1)
    consistent = [
        index
        for index, status in enumerate(statuses)
        if status in {"primary", "consistent"}
    ]
    ambiguous = [
        index for index, status in enumerate(statuses) if status == "ambiguous"
    ]
    conflicting = [
        index for index, status in enumerate(statuses) if status == "conflicting"
    ]
    return MultiReferenceFusionEvidence(
        reference_count=len(statuses),
        primary_index=primary_index,
        consensus_confidence=0.90,
        conflict_detected=bool(conflicting),
        consistent_indices=consistent,
        ambiguous_indices=ambiguous,
        conflicting_indices=conflicting,
        references=[
            ReferenceEvidence(
                index=index,
                sha256=f"{index + 1:064x}",
                quality_score=0.90,
                similarity_to_primary=similarities[index],
                status=status,
            )
            for index, status in enumerate(statuses)
        ],
    )


def test_primary_reference_uses_identity_alignment() -> None:
    bbox = BoundingBox(x=0.2, y=0.1, width=0.6, height=0.7)
    evidence = build_reference_alignment(
        [_preflight(index=0, size=(1000, 800), bbox=bbox)],
        _fusion(statuses=["primary"]),
    )

    item = evidence.references[0]
    assert item.disposition is ReferenceAlignmentDisposition.IDENTITY
    assert item.transform_matrix == [
        1.0, 0.0, 0.0,
        0.0, 1.0, 0.0,
        0.0, 0.0, 1.0,
    ]
    assert item.execution_enabled is False
    assert evidence.aligned_indices == [0]


def test_consistent_bbox_geometry_builds_deterministic_affine_candidate() -> None:
    primary_bbox = BoundingBox(x=0.2, y=0.1, width=0.6, height=0.6)
    secondary_bbox = BoundingBox(x=0.1, y=0.2, width=0.5, height=0.5)
    preflights = [
        _preflight(index=0, size=(1000, 800), bbox=primary_bbox),
        _preflight(index=1, size=(1200, 960), bbox=secondary_bbox),
    ]
    evidence = build_reference_alignment(
        preflights,
        _fusion(statuses=["primary", "consistent"]),
    )

    item = evidence.references[1]
    assert item.disposition is ReferenceAlignmentDisposition.AFFINE_CANDIDATE
    assert item.fail_closed is False
    assert item.execution_enabled is False
    assert item.reprojection_error is None
    assert item.transform_matrix == [
        1.2, 0.0, 0.08,
        0.0, 1.2, -0.14,
        0.0, 0.0, 1.0,
    ]
    assert "bbox_correspondence_only" in item.reason_codes
    assert evidence.affine_candidate_count == 1
    assert evidence.aligned_indices == [0, 1]


def test_perspective_candidate_requires_real_correspondence_and_fails_closed() -> None:
    bbox = BoundingBox(x=0.15, y=0.15, width=0.7, height=0.7)
    evidence = build_reference_alignment(
        [
            _preflight(index=0, size=(1000, 1000), bbox=bbox),
            _preflight(index=1, size=(1000, 1000), bbox=bbox),
        ],
        _fusion(statuses=["primary", "consistent"]),
        perspective_severity=[0.0, 0.20],
    )

    item = evidence.references[1]
    assert item.disposition is ReferenceAlignmentDisposition.HOMOGRAPHY_CANDIDATE
    assert item.transform_matrix == []
    assert item.reprojection_error is None
    assert item.fail_closed is True
    assert "need_feature_correspondence" in item.missing_capabilities
    assert evidence.homography_candidate_count == 1
    assert evidence.execution_enabled is False


def test_occlusion_or_severe_perspective_requires_semantic_registration() -> None:
    bbox = BoundingBox(x=0.1, y=0.1, width=0.8, height=0.8)
    occluded = build_reference_alignment(
        [
            _preflight(index=0, size=(1000, 1000), bbox=bbox),
            _preflight(index=1, size=(1000, 1000), bbox=bbox),
        ],
        _fusion(statuses=["primary", "consistent"]),
        occlusion=[0.0, 0.20],
    )
    severe = build_reference_alignment(
        [
            _preflight(index=0, size=(1000, 1000), bbox=bbox),
            _preflight(index=1, size=(1000, 1000), bbox=bbox),
        ],
        _fusion(statuses=["primary", "consistent"]),
        perspective_severity=[0.0, 0.40],
    )

    assert (
        occluded.references[1].disposition
        is ReferenceAlignmentDisposition.SEMANTIC_REQUIRED
    )
    assert (
        severe.references[1].disposition
        is ReferenceAlignmentDisposition.SEMANTIC_REQUIRED
    )
    assert occluded.references[1].fail_closed is True
    assert severe.references[1].fail_closed is True


def test_conflicting_and_aspect_incompatible_references_are_excluded() -> None:
    primary_bbox = BoundingBox(x=0.1, y=0.1, width=0.8, height=0.8)
    conflicting = build_reference_alignment(
        [
            _preflight(index=0, size=(1000, 1000), bbox=primary_bbox),
            _preflight(index=1, size=(1000, 1000), bbox=primary_bbox),
        ],
        _fusion(statuses=["primary", "conflicting"]),
    )
    aspect = build_reference_alignment(
        [
            _preflight(index=0, size=(1000, 1000), bbox=primary_bbox),
            _preflight(
                index=1,
                size=(1000, 1000),
                bbox=BoundingBox(x=0.1, y=0.1, width=0.8, height=0.25),
            ),
        ],
        _fusion(statuses=["primary", "consistent"]),
    )

    assert (
        conflicting.references[1].disposition
        is ReferenceAlignmentDisposition.MANUAL_REVIEW
    )
    assert (
        aspect.references[1].disposition
        is ReferenceAlignmentDisposition.SEMANTIC_REQUIRED
    )
    assert conflicting.excluded_indices == [1]
    assert aspect.excluded_indices == [1]


def _art(path: Path, offset: int = 0) -> Path:
    image = Image.new("RGBA", (480, 360), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (110 + offset, 70, 370 + offset, 290),
        fill=(45, 125, 210, 255),
    )
    draw.ellipse(
        (180 + offset, 120, 300 + offset, 240),
        outline=(255, 255, 255, 255),
        width=12,
    )
    image.save(path)
    return path


def test_engine_persists_alignment_evidence_without_pixel_operation(
    tmp_path: Path,
) -> None:
    first = _art(tmp_path / "first.png")
    second = _art(tmp_path / "second.png", offset=2)
    engine = Engine(Settings(data_root=tmp_path / "runtime"))
    job = engine.create_job([first, second], QualityMode.QUICK_2D)

    result = engine.run_job(job.job_id)

    assert result.state in {JobState.COMPLETED, JobState.REVIEW_REQUIRED}
    alignment = engine.checkpoints.payload(job.job_id, "reference_alignment")
    assert alignment["method"] == "geometric_reference_alignment_v1"
    assert alignment["execution_enabled"] is False
    assert len(alignment["references"]) == 2

    candidate = engine.checkpoints.payload(job.job_id, "candidate")
    assert "reference_alignment" not in (candidate.get("precision_ops") or [])

    manifest_path = (
        engine.settings.jobs_dir
        / job.job_id
        / "master"
        / "artifact_manifest.json"
    )
    assert manifest_path.is_file()
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert '"reference_alignment"' in manifest_text
