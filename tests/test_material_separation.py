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
    PreflightResult,
    QualityMode,
)
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.material_separation import build_material_separation_evidence
from pod_artwork_engine.settings import Settings


def _preflight(
    path: Path,
    *,
    bbox: BoundingBox | None = None,
    has_alpha: bool = False,
    artwork_confidence: float = 0.90,
    source_quality: float = 0.90,
    compression_risk: float = 0.0,
) -> PreflightResult:
    with Image.open(path) as image:
        width, height = image.size
    return PreflightResult(
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        filename=path.name,
        width=width,
        height=height,
        image_mode="RGBA" if has_alpha else "RGB",
        image_format="PNG",
        file_size_bytes=path.stat().st_size,
        has_alpha=has_alpha,
        source_quality=source_quality,
        compression_risk=compression_risk,
        artwork_bbox=bbox or BoundingBox(x=0.25, y=0.20, width=0.50, height=0.60),
        artwork_confidence=artwork_confidence,
    )


def _flat_background(path: Path, *, mode: str = "RGB") -> Path:
    background = (250, 250, 250, 255) if mode == "RGBA" else (250, 250, 250)
    foreground = (25, 55, 95, 255) if mode == "RGBA" else (25, 55, 95)
    image = Image.new(mode, (480, 360), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((130, 95, 350, 285), fill=foreground)
    image.save(path)
    return path


def test_meaningful_source_alpha_is_preferred(tmp_path: Path) -> None:
    path = tmp_path / "alpha.png"
    image = Image.new("RGBA", (480, 360), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((130, 80, 350, 300), fill=(20, 80, 180, 255))
    image.save(path)

    evidence = build_material_separation_evidence(
        path,
        _preflight(path, has_alpha=True),
        DesignSpec(
            artwork_type=ArtworkType.LOGO,
            artwork_bbox=BoundingBox(x=0.25, y=0.20, width=0.50, height=0.65),
            confidence=0.95,
        ),
        primary_index=0,
    )

    assert evidence.disposition is MaterialSeparationDisposition.EXISTING_ALPHA
    assert evidence.meaningful_alpha is True
    assert evidence.transparent_fraction > 0.20
    assert evidence.fail_closed is False
    assert "meaningful_source_alpha" in evidence.reason_codes


def test_uniform_border_background_is_simple_extraction_candidate(
    tmp_path: Path,
) -> None:
    path = _flat_background(tmp_path / "flat.png")

    evidence = build_material_separation_evidence(
        path,
        _preflight(path),
        DesignSpec(
            artwork_type=ArtworkType.LOGO,
            artwork_bbox=BoundingBox(x=0.25, y=0.20, width=0.50, height=0.60),
            confidence=0.92,
        ),
        primary_index=0,
    )

    assert (
        evidence.disposition
        is MaterialSeparationDisposition.SIMPLE_BORDER_BACKGROUND
    )
    assert evidence.border_uniformity >= 0.90
    assert evidence.edge_contact_ratio == 0
    assert evidence.foreground_contrast >= 0.10
    assert evidence.fail_closed is False


def test_opaque_rgba_does_not_count_as_meaningful_alpha(tmp_path: Path) -> None:
    path = _flat_background(tmp_path / "opaque-rgba.png", mode="RGBA")

    evidence = build_material_separation_evidence(
        path,
        _preflight(path, has_alpha=True),
        DesignSpec(
            artwork_type=ArtworkType.LOGO,
            artwork_bbox=BoundingBox(x=0.25, y=0.20, width=0.50, height=0.60),
            confidence=0.92,
        ),
        primary_index=0,
    )

    assert evidence.meaningful_alpha is False
    assert (
        evidence.disposition
        is MaterialSeparationDisposition.SIMPLE_BORDER_BACKGROUND
    )
    assert "declared_alpha_not_meaningful" in evidence.reason_codes


def test_nonuniform_background_requires_semantic_separation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gradient.png"
    image = Image.new("RGB", (480, 360))
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            amount = x / max(1, image.width - 1)
            pixels[x, y] = (
                round(25 + 210 * amount),
                round(220 - 160 * amount),
                round(60 + 120 * amount),
            )
    draw = ImageDraw.Draw(image)
    draw.rectangle((130, 95, 350, 285), fill=(5, 5, 5))
    image.save(path)

    evidence = build_material_separation_evidence(
        path,
        _preflight(path),
        DesignSpec(
            artwork_type=ArtworkType.TYPOGRAPHY,
            artwork_bbox=BoundingBox(x=0.25, y=0.20, width=0.50, height=0.60),
            confidence=0.90,
        ),
        primary_index=0,
    )

    assert evidence.disposition is MaterialSeparationDisposition.SEMANTIC_REQUIRED
    assert evidence.border_uniformity < 0.90
    assert evidence.fail_closed is True
    assert "nonuniform_background" in evidence.reason_codes


def test_edge_touching_artwork_fails_closed_to_semantic_separation(
    tmp_path: Path,
) -> None:
    path = _flat_background(tmp_path / "edge.png")
    bbox = BoundingBox(x=0.0, y=0.20, width=0.60, height=0.60)

    evidence = build_material_separation_evidence(
        path,
        _preflight(path, bbox=bbox),
        DesignSpec(
            artwork_type=ArtworkType.LOGO,
            artwork_bbox=bbox,
            confidence=0.92,
        ),
        primary_index=0,
    )

    assert evidence.disposition is MaterialSeparationDisposition.SEMANTIC_REQUIRED
    assert evidence.edge_contact_ratio > 0
    assert evidence.fail_closed is True
    assert "artwork_touches_source_edge" in evidence.reason_codes


def test_fine_detail_illustration_requires_semantic_separation(
    tmp_path: Path,
) -> None:
    path = _flat_background(tmp_path / "detail.png")

    evidence = build_material_separation_evidence(
        path,
        _preflight(path),
        DesignSpec(
            artwork_type=ArtworkType.ILLUSTRATION,
            artwork_bbox=BoundingBox(x=0.25, y=0.20, width=0.50, height=0.60),
            texture_classes=["fine_detail"],
            confidence=0.90,
        ),
        primary_index=0,
    )

    assert evidence.disposition is MaterialSeparationDisposition.SEMANTIC_REQUIRED
    assert evidence.fail_closed is True
    assert "illustration_or_mixed_material" in evidence.reason_codes
    assert "fine_detail_edges" in evidence.reason_codes


def test_weak_localization_requires_manual_review(tmp_path: Path) -> None:
    path = _flat_background(tmp_path / "weak.png")

    evidence = build_material_separation_evidence(
        path,
        _preflight(path, artwork_confidence=0.20),
        DesignSpec(
            artwork_type=ArtworkType.LOGO,
            artwork_bbox=BoundingBox(x=0.25, y=0.20, width=0.50, height=0.60),
            confidence=0.40,
        ),
        primary_index=0,
    )

    assert evidence.disposition is MaterialSeparationDisposition.MANUAL_REVIEW
    assert evidence.fail_closed is True
    assert "artwork_localization_low_confidence" in evidence.reason_codes
    assert "need_manual_review" in evidence.missing_capabilities


def test_missing_bbox_requires_manual_review(tmp_path: Path) -> None:
    path = _flat_background(tmp_path / "missing-bbox.png")
    preflight = _preflight(path).model_copy(update={"artwork_bbox": None})

    evidence = build_material_separation_evidence(
        path,
        preflight,
        DesignSpec(
            artwork_type=ArtworkType.UNKNOWN,
            artwork_bbox=None,
            confidence=0.20,
        ),
        primary_index=0,
    )

    assert evidence.disposition is MaterialSeparationDisposition.MANUAL_REVIEW
    assert evidence.fail_closed is True
    assert evidence.artwork_bbox is None
    assert "artwork_bbox_unavailable" in evidence.reason_codes


def test_engine_persists_material_evidence_without_changing_renderer(
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
    evidence = engine.checkpoints.payload(job.job_id, "material_separation")
    assert evidence["method"] == "material_separation_evidence_v1"
    assert evidence["disposition"] == "existing_alpha"

    candidate = engine.checkpoints.payload(job.job_id, "candidate")
    assert "material_separation" not in (candidate.get("precision_ops") or [])

    manifest_path = (
        engine.settings.jobs_dir
        / job.job_id
        / "master"
        / "artifact_manifest.json"
    )
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert '"material_separation"' in manifest_text
