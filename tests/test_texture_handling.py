from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw

from pod_artwork_engine.contracts import (
    ArtworkType,
    BoundingBox,
    DesignSpec,
    JobState,
    MaterialSeparationDisposition,
    MaterialSeparationEvidence,
    PreflightResult,
    QualityMode,
    RegionConfidenceMapEvidence,
    RegionEvidenceCell,
    TextureHandlingDisposition,
)
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.texture_handling import build_texture_handling_evidence


def _bbox() -> BoundingBox:
    return BoundingBox(x=0.10, y=0.10, width=0.80, height=0.80)


def _make_pattern(path: Path, *, size: tuple[int, int] = (1400, 1000)) -> Path:
    image = Image.new("RGB", size, (245, 245, 245))
    draw = ImageDraw.Draw(image)
    for y in range(100, size[1] - 100, 24):
        for x in range(140, size[0] - 140, 24):
            if ((x // 24) + (y // 24)) % 2:
                draw.rectangle((x, y, x + 11, y + 11), fill=(20, 20, 20))
    image.save(path)
    return path


def _make_smooth(path: Path, *, size: tuple[int, int] = (1400, 1000)) -> Path:
    image = Image.new("RGB", size, (240, 240, 240))
    draw = ImageDraw.Draw(image)
    draw.ellipse((250, 160, 1150, 840), fill=(80, 130, 200))
    image.save(path)
    return path


def _preflight(
    path: Path,
    *,
    source_quality: float = 0.90,
    compression_risk: float = 0.10,
    artwork_confidence: float = 0.92,
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
        has_alpha=False,
        source_quality=source_quality,
        compression_risk=compression_risk,
        artwork_bbox=_bbox(),
        artwork_confidence=artwork_confidence,
    )


def _material(
    disposition: MaterialSeparationDisposition = (
        MaterialSeparationDisposition.SIMPLE_BORDER_BACKGROUND
    ),
    *,
    confidence: float = 0.90,
) -> MaterialSeparationEvidence:
    return MaterialSeparationEvidence(
        disposition=disposition,
        primary_index=0,
        confidence=confidence,
        fail_closed=disposition
        in {
            MaterialSeparationDisposition.SEMANTIC_REQUIRED,
            MaterialSeparationDisposition.MANUAL_REVIEW,
        },
        artwork_bbox=_bbox(),
        border_uniformity=0.95,
        foreground_contrast=0.40,
    )


def _region_map(
    *,
    mean_confidence: float = 0.90,
    available: bool = True,
) -> RegionConfidenceMapEvidence:
    if not available:
        return RegionConfidenceMapEvidence(
            primary_index=0,
            mean_confidence=0,
            minimum_confidence=0,
            support_coverage=0,
            low_confidence_cells=0,
            reason_codes=["region_evidence_unavailable"],
        )

    cells = [
        RegionEvidenceCell(
            row=row,
            column=column,
            bbox=BoundingBox(
                x=column / 4,
                y=row / 4,
                width=0.25,
                height=0.25,
            ),
            confidence=mean_confidence,
            agreement=mean_confidence,
            support_count=1,
            comparison_count=0,
        )
        for row in range(4)
        for column in range(4)
    ]
    return RegionConfidenceMapEvidence(
        primary_index=0,
        mean_confidence=mean_confidence,
        minimum_confidence=mean_confidence,
        support_coverage=1.0,
        low_confidence_cells=0 if mean_confidence >= 0.60 else 16,
        cells=cells,
    )


def test_outlined_high_quality_texture_is_local_enhancement_candidate(
    tmp_path: Path,
) -> None:
    path = _make_pattern(tmp_path / "outlined.png")

    evidence = build_texture_handling_evidence(
        path,
        _preflight(path),
        DesignSpec(
            artwork_type=ArtworkType.LOGO,
            artwork_bbox=_bbox(),
            texture_classes=["outlined"],
            confidence=0.94,
        ),
        _material(),
        _region_map(),
        primary_index=0,
    )

    assert (
        evidence.disposition
        is TextureHandlingDisposition.LOCAL_DETAIL_ENHANCEMENT_CANDIDATE
    )
    assert evidence.fail_closed is False
    assert evidence.edge_density >= 0.08
    assert evidence.local_contrast >= 0.12
    assert evidence.local_variation >= 0.03
    assert evidence.native_long_edge >= 900
    assert "outlined_detail_local_enhancement_candidate" in evidence.reason_codes


def test_painterly_texture_is_preserved_as_raster(tmp_path: Path) -> None:
    path = _make_smooth(tmp_path / "painterly.png")

    evidence = build_texture_handling_evidence(
        path,
        _preflight(path),
        DesignSpec(
            artwork_type=ArtworkType.ILLUSTRATION,
            artwork_bbox=_bbox(),
            texture_classes=["smooth_or_painterly"],
            confidence=0.92,
        ),
        _material(),
        _region_map(),
        primary_index=0,
    )

    assert evidence.disposition is TextureHandlingDisposition.PRESERVE_RASTER
    assert evidence.fail_closed is False
    assert "painterly_texture_preserve_raster" in evidence.reason_codes


def test_fine_detail_low_quality_requires_semantic_reconstruction(
    tmp_path: Path,
) -> None:
    path = _make_pattern(tmp_path / "fine-low.png")

    evidence = build_texture_handling_evidence(
        path,
        _preflight(path, source_quality=0.55, compression_risk=0.50),
        DesignSpec(
            artwork_type=ArtworkType.ILLUSTRATION,
            artwork_bbox=_bbox(),
            texture_classes=["fine_detail", "source_compression"],
            confidence=0.65,
        ),
        _material(),
        _region_map(),
        primary_index=0,
    )

    assert evidence.disposition is TextureHandlingDisposition.SEMANTIC_REQUIRED
    assert evidence.fail_closed is True
    assert "fine_detail_source_insufficient" in evidence.reason_codes
    assert "need_texture_reconstruction" in evidence.missing_capabilities


def test_fine_detail_high_quality_is_preserved_without_unbenchmarked_sharpening(
    tmp_path: Path,
) -> None:
    path = _make_pattern(tmp_path / "fine-high.png", size=(1800, 1400))

    evidence = build_texture_handling_evidence(
        path,
        _preflight(path, source_quality=0.92, compression_risk=0.05),
        DesignSpec(
            artwork_type=ArtworkType.ILLUSTRATION,
            artwork_bbox=_bbox(),
            texture_classes=["fine_detail"],
            confidence=0.94,
        ),
        _material(),
        _region_map(mean_confidence=0.92),
        primary_index=0,
    )

    assert evidence.disposition is TextureHandlingDisposition.PRESERVE_RASTER
    assert evidence.fail_closed is False
    assert (
        "fine_detail_preserve_without_unbenchmarked_sharpening"
        in evidence.reason_codes
    )


def test_material_semantic_requirement_short_circuits_texture_planning(
    tmp_path: Path,
) -> None:
    path = tmp_path / "not-needed.png"

    evidence = build_texture_handling_evidence(
        path,
        PreflightResult(
            sha256="0" * 64,
            filename=path.name,
            width=1000,
            height=1000,
            image_mode="RGB",
            file_size_bytes=0,
            source_quality=0.90,
            artwork_bbox=_bbox(),
            artwork_confidence=0.90,
        ),
        DesignSpec(
            artwork_type=ArtworkType.ILLUSTRATION,
            artwork_bbox=_bbox(),
            texture_classes=["outlined"],
            confidence=0.90,
        ),
        _material(MaterialSeparationDisposition.SEMANTIC_REQUIRED),
        _region_map(),
        primary_index=0,
    )

    assert evidence.disposition is TextureHandlingDisposition.SEMANTIC_REQUIRED
    assert evidence.fail_closed is True
    assert evidence.edge_density == 0
    assert (
        "material_separation_requires_semantic_handling"
        in evidence.reason_codes
    )


def test_material_manual_review_short_circuits_texture_planning(
    tmp_path: Path,
) -> None:
    path = tmp_path / "not-needed-manual.png"

    evidence = build_texture_handling_evidence(
        path,
        PreflightResult(
            sha256="1" * 64,
            filename=path.name,
            width=1000,
            height=1000,
            image_mode="RGB",
            file_size_bytes=0,
            source_quality=0.90,
            artwork_bbox=_bbox(),
            artwork_confidence=0.90,
        ),
        DesignSpec(
            artwork_type=ArtworkType.UNKNOWN,
            artwork_bbox=_bbox(),
            confidence=0.50,
        ),
        _material(MaterialSeparationDisposition.MANUAL_REVIEW, confidence=0.30),
        _region_map(),
        primary_index=0,
    )

    assert evidence.disposition is TextureHandlingDisposition.MANUAL_REVIEW
    assert evidence.fail_closed is True
    assert "material_separation_unresolved" in evidence.reason_codes


def test_unavailable_region_evidence_requires_manual_review(tmp_path: Path) -> None:
    path = _make_smooth(tmp_path / "region-unavailable.png")

    evidence = build_texture_handling_evidence(
        path,
        _preflight(path),
        DesignSpec(
            artwork_type=ArtworkType.ILLUSTRATION,
            artwork_bbox=_bbox(),
            texture_classes=["smooth_or_painterly"],
            confidence=0.90,
        ),
        _material(),
        _region_map(available=False),
        primary_index=0,
    )

    assert evidence.disposition is TextureHandlingDisposition.MANUAL_REVIEW
    assert evidence.fail_closed is True
    assert "regional_texture_evidence_insufficient" in evidence.reason_codes


def test_perspective_texture_risk_requires_semantic_handling(tmp_path: Path) -> None:
    path = _make_smooth(tmp_path / "perspective.png")

    evidence = build_texture_handling_evidence(
        path,
        _preflight(path),
        DesignSpec(
            artwork_type=ArtworkType.ILLUSTRATION,
            artwork_bbox=_bbox(),
            texture_classes=["smooth_or_painterly"],
            perspective_severity=0.35,
            confidence=0.90,
        ),
        _material(),
        _region_map(),
        primary_index=0,
    )

    assert evidence.disposition is TextureHandlingDisposition.SEMANTIC_REQUIRED
    assert evidence.fail_closed is True
    assert "perspective_texture_risk" in evidence.reason_codes


def test_engine_persists_texture_evidence_without_changing_renderer(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    image = Image.new("RGBA", (480, 360), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((120, 70, 360, 290), fill=(40, 130, 220, 255))
    image.save(source)

    engine = Engine(Settings(data_root=tmp_path / "runtime"))
    job = engine.create_job([source], QualityMode.QUICK_2D)
    result = engine.run_job(job.job_id)

    assert result.state in {JobState.COMPLETED, JobState.REVIEW_REQUIRED}
    evidence = engine.checkpoints.payload(job.job_id, "texture_handling")
    assert evidence["method"] == "texture_handling_evidence_v1"
    assert evidence["disposition"] in {
        "preserve_raster",
        "local_detail_enhancement_candidate",
        "semantic_required",
        "manual_review",
    }

    candidate = engine.checkpoints.payload(job.job_id, "candidate")
    assert "texture_handling" not in (candidate.get("precision_ops") or [])

    manifest_path = (
        engine.settings.jobs_dir
        / job.job_id
        / "master"
        / "artifact_manifest.json"
    )
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert '"texture_handling"' in manifest_text
