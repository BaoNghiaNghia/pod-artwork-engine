from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from pod_artwork_engine.contracts import (
    ArtworkType,
    BoundingBox,
    DesignSpec,
    ExportProfile,
    JobState,
    MaterialSeparationDisposition,
    MaterialSeparationEvidence,
    PreflightResult,
    QualityMode,
    RegionConfidenceMapEvidence,
    RegionEvidenceCell,
    SuperResolutionDisposition,
    TextureHandlingDisposition,
    TextureHandlingEvidence,
)
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.super_resolution import build_super_resolution_readiness


def _bbox() -> BoundingBox:
    return BoundingBox(x=0.10, y=0.10, width=0.80, height=0.80)


def _preflight(
    *,
    width: int,
    height: int,
    source_quality: float = 0.90,
    compression_risk: float = 0.10,
) -> PreflightResult:
    return PreflightResult(
        sha256="a" * 64,
        filename="source.png",
        width=width,
        height=height,
        image_mode="RGB",
        image_format="PNG",
        file_size_bytes=1,
        source_quality=source_quality,
        compression_risk=compression_risk,
        artwork_bbox=_bbox(),
        artwork_confidence=0.92,
    )


def _material(
    disposition: MaterialSeparationDisposition = (
        MaterialSeparationDisposition.SIMPLE_BORDER_BACKGROUND
    ),
) -> MaterialSeparationEvidence:
    return MaterialSeparationEvidence(
        disposition=disposition,
        primary_index=0,
        confidence=0.90,
        fail_closed=disposition
        in {
            MaterialSeparationDisposition.SEMANTIC_REQUIRED,
            MaterialSeparationDisposition.MANUAL_REVIEW,
        },
        artwork_bbox=_bbox(),
        border_uniformity=0.95,
        foreground_contrast=0.40,
    )


def _texture(
    disposition: TextureHandlingDisposition,
    *,
    confidence: float = 0.90,
) -> TextureHandlingEvidence:
    return TextureHandlingEvidence(
        disposition=disposition,
        primary_index=0,
        confidence=confidence,
        fail_closed=disposition
        in {
            TextureHandlingDisposition.SEMANTIC_REQUIRED,
            TextureHandlingDisposition.MANUAL_REVIEW,
        },
        artwork_bbox=_bbox(),
        native_long_edge=2000,
        source_quality=0.90,
        compression_risk=0.10,
        region_mean_confidence=0.90,
        material_disposition=MaterialSeparationDisposition.SIMPLE_BORDER_BACKGROUND,
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


def _design() -> DesignSpec:
    return DesignSpec(
        artwork_type=ArtworkType.ILLUSTRATION,
        artwork_bbox=_bbox(),
        texture_classes=["outlined"],
        confidence=0.92,
    )


def _build(
    preflight: PreflightResult,
    texture: TextureHandlingEvidence,
    *,
    material: MaterialSeparationEvidence | None = None,
    region_map: RegionConfidenceMapEvidence | None = None,
    provider_available: bool = True,
):
    return build_super_resolution_readiness(
        preflight,
        _design(),
        material or _material(),
        texture,
        region_map or _region_map(),
        profile=ExportProfile(),
        required_native_long_edge=1000,
        primary_index=0,
        provider_available=provider_available,
    )


def test_native_resolution_is_sufficient_when_print_scale_is_small() -> None:
    evidence = _build(
        _preflight(width=5200, height=5200),
        _texture(TextureHandlingDisposition.PRESERVE_RASTER),
    )

    assert evidence.disposition is SuperResolutionDisposition.NATIVE_SUFFICIENT
    assert evidence.fail_closed is False
    assert evidence.execution_enabled is False
    assert evidence.estimated_scale_factor <= 1.25
    assert evidence.native_long_edge >= evidence.required_native_long_edge
    assert "native_resolution_sufficient_for_print_target" in evidence.reason_codes


def test_high_quality_outlined_art_is_local_sr_candidate() -> None:
    evidence = _build(
        _preflight(width=3000, height=3000),
        _texture(TextureHandlingDisposition.LOCAL_DETAIL_ENHANCEMENT_CANDIDATE),
    )

    assert evidence.disposition is SuperResolutionDisposition.LOCAL_SR_CANDIDATE
    assert evidence.fail_closed is False
    assert 1.25 < evidence.estimated_scale_factor <= 2.0
    assert "need_local_sr_benchmark" in evidence.missing_capabilities
    assert "bounded_local_sr_benchmark_candidate" in evidence.reason_codes


def test_moderate_scale_preserved_raster_is_remote_sr_candidate() -> None:
    evidence = _build(
        _preflight(width=2000, height=2000),
        _texture(TextureHandlingDisposition.PRESERVE_RASTER),
        provider_available=True,
    )

    assert evidence.disposition is SuperResolutionDisposition.REMOTE_SR_CANDIDATE
    assert evidence.fail_closed is False
    assert 2.0 < evidence.estimated_scale_factor <= 4.0
    assert evidence.provider_available is True
    assert "need_remote_sr_benchmark" in evidence.missing_capabilities


def test_remote_sr_candidate_fails_closed_when_provider_is_unavailable() -> None:
    evidence = _build(
        _preflight(width=2000, height=2000),
        _texture(TextureHandlingDisposition.PRESERVE_RASTER),
        provider_available=False,
    )

    assert evidence.disposition is SuperResolutionDisposition.REMOTE_SR_CANDIDATE
    assert evidence.fail_closed is True
    assert "remote_sr_provider_unavailable" in evidence.reason_codes
    assert "need_remote_sr_provider" in evidence.missing_capabilities


def test_extreme_scale_factor_requires_higher_resolution_reference() -> None:
    evidence = _build(
        _preflight(width=1000, height=1000),
        _texture(TextureHandlingDisposition.PRESERVE_RASTER),
    )

    assert evidence.disposition is SuperResolutionDisposition.MANUAL_REVIEW
    assert evidence.fail_closed is True
    assert evidence.estimated_scale_factor > 4.0
    assert (
        "required_scale_factor_exceeds_safe_sr_readiness"
        in evidence.reason_codes
    )
    assert "need_higher_resolution_reference" in evidence.missing_capabilities


def test_weak_or_compressed_source_is_not_sent_to_sr_automatically() -> None:
    evidence = _build(
        _preflight(
            width=2400,
            height=2400,
            source_quality=0.40,
            compression_risk=0.60,
        ),
        _texture(TextureHandlingDisposition.PRESERVE_RASTER),
    )

    assert evidence.disposition is SuperResolutionDisposition.MANUAL_REVIEW
    assert evidence.fail_closed is True
    assert "source_quality_too_low_for_sr" in evidence.reason_codes
    assert "compression_too_high_for_sr" in evidence.reason_codes


def test_semantic_texture_dependency_blocks_sr_readiness() -> None:
    evidence = _build(
        _preflight(width=2400, height=2400),
        _texture(TextureHandlingDisposition.SEMANTIC_REQUIRED),
    )

    assert evidence.disposition is SuperResolutionDisposition.MANUAL_REVIEW
    assert evidence.fail_closed is True
    assert "texture_requires_semantic_handling" in evidence.reason_codes
    assert "need_semantic_reconstruction" in evidence.missing_capabilities


def test_unresolved_material_dependency_blocks_sr_readiness() -> None:
    evidence = _build(
        _preflight(width=2400, height=2400),
        _texture(TextureHandlingDisposition.PRESERVE_RASTER),
        material=_material(MaterialSeparationDisposition.MANUAL_REVIEW),
    )

    assert evidence.disposition is SuperResolutionDisposition.MANUAL_REVIEW
    assert evidence.fail_closed is True
    assert "material_separation_unresolved" in evidence.reason_codes


def test_unavailable_region_evidence_blocks_sr_readiness() -> None:
    evidence = _build(
        _preflight(width=2400, height=2400),
        _texture(TextureHandlingDisposition.PRESERVE_RASTER),
        region_map=_region_map(available=False),
    )

    assert evidence.disposition is SuperResolutionDisposition.MANUAL_REVIEW
    assert evidence.fail_closed is True
    assert "regional_sr_evidence_insufficient" in evidence.reason_codes


def test_engine_persists_sr_readiness_without_changing_renderer(
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
    evidence = engine.checkpoints.payload(
        job.job_id,
        "super_resolution_readiness",
    )
    assert evidence["method"] == "super_resolution_readiness_v1"
    assert evidence["execution_enabled"] is False
    assert evidence["disposition"] in {
        "native_sufficient",
        "local_sr_candidate",
        "remote_sr_candidate",
        "manual_review",
    }

    candidate = engine.checkpoints.payload(job.job_id, "candidate")
    assert "super_resolution" not in (candidate.get("precision_ops") or [])
    assert "local_sr" not in (candidate.get("precision_ops") or [])
    assert "remote_sr" not in (candidate.get("precision_ops") or [])

    manifest_path = (
        engine.settings.jobs_dir
        / job.job_id
        / "master"
        / "artifact_manifest.json"
    )
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert '"super_resolution_readiness"' in manifest_text
