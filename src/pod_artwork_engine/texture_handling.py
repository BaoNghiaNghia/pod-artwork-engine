from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

from .artwork_detection import crop_artwork
from .contracts import (
    DesignSpec,
    MaterialSeparationDisposition,
    MaterialSeparationEvidence,
    PreflightResult,
    RegionConfidenceMapEvidence,
    TextureHandlingDisposition,
    TextureHandlingEvidence,
)


FINE_DETAIL_QUALITY_MIN = 0.70
FINE_DETAIL_COMPRESSION_MAX = 0.40
FINE_DETAIL_NATIVE_EDGE_MIN = 1200
LOCAL_ENHANCE_QUALITY_MIN = 0.65
LOCAL_ENHANCE_COMPRESSION_MAX = 0.35
LOCAL_ENHANCE_NATIVE_EDGE_MIN = 900
REGION_CONFIDENCE_MIN = 0.45
PERSPECTIVE_RISK = 0.10
OCCLUSION_RISK = 0.10


def _texture_statistics(
    source_path: Path,
    preflight: PreflightResult,
    design_spec: DesignSpec,
) -> tuple[float, float, float, int]:
    bbox = design_spec.artwork_bbox or preflight.artwork_bbox
    if bbox is None:
        raise ValueError("artwork bounding box is unavailable for texture evidence")

    crop = crop_artwork(source_path, bbox).convert("L")
    native_long_edge = max(crop.width, crop.height)
    working = crop.copy()
    working.thumbnail((320, 320), Image.Resampling.LANCZOS)

    values = list(working.getdata())
    if not values:
        raise ValueError("artwork crop is empty")

    edges = working.filter(ImageFilter.FIND_EDGES)
    edge_values = list(edges.getdata())
    edge_density = sum(value >= 48 for value in edge_values) / max(
        1,
        len(edge_values),
    )

    contrast = min(1.0, float(ImageStat.Stat(working).stddev[0]) / 64.0)

    width, height = working.size
    if width < 2 or height < 2:
        variation = 0.0
    else:
        total_difference = 0
        comparisons = 0
        for row in range(height):
            offset = row * width
            for column in range(width - 1):
                total_difference += abs(
                    values[offset + column] - values[offset + column + 1]
                )
                comparisons += 1
        for row in range(height - 1):
            offset = row * width
            next_offset = (row + 1) * width
            for column in range(width):
                total_difference += abs(
                    values[offset + column] - values[next_offset + column]
                )
                comparisons += 1
        variation = min(
            1.0,
            (total_difference / max(1, comparisons)) / 48.0,
        )

    return (
        max(0.0, min(1.0, edge_density)),
        max(0.0, min(1.0, contrast)),
        max(0.0, min(1.0, variation)),
        native_long_edge,
    )


def build_texture_handling_evidence(
    source_path: Path,
    preflight: PreflightResult,
    design_spec: DesignSpec,
    material: MaterialSeparationEvidence,
    region_map: RegionConfidenceMapEvidence,
    *,
    primary_index: int,
) -> TextureHandlingEvidence:
    bbox = design_spec.artwork_bbox or preflight.artwork_bbox
    common = {
        "primary_index": primary_index,
        "artwork_bbox": bbox,
        "source_quality": preflight.source_quality,
        "compression_risk": preflight.compression_risk,
        "region_mean_confidence": region_map.mean_confidence,
        "material_disposition": material.disposition,
    }

    if bbox is None:
        return TextureHandlingEvidence(
            disposition=TextureHandlingDisposition.MANUAL_REVIEW,
            confidence=0.0,
            fail_closed=True,
            reason_codes=["artwork_bbox_unavailable"],
            missing_capabilities=["need_artwork_localization", "need_manual_review"],
            **common,
        )

    if material.disposition is MaterialSeparationDisposition.MANUAL_REVIEW:
        return TextureHandlingEvidence(
            disposition=TextureHandlingDisposition.MANUAL_REVIEW,
            confidence=max(0.0, min(0.49, material.confidence)),
            fail_closed=True,
            reason_codes=["material_separation_unresolved"],
            missing_capabilities=[
                "need_material_separation_verification",
                "need_manual_review",
            ],
            **common,
        )

    if material.disposition is MaterialSeparationDisposition.SEMANTIC_REQUIRED:
        return TextureHandlingEvidence(
            disposition=TextureHandlingDisposition.SEMANTIC_REQUIRED,
            confidence=max(0.50, material.confidence),
            fail_closed=True,
            reason_codes=["material_separation_requires_semantic_handling"],
            missing_capabilities=[
                "need_semantic_reconstruction",
                "need_texture_reconstruction",
            ],
            **common,
        )

    if (
        "region_evidence_unavailable" in region_map.reason_codes
        or not region_map.cells
        or region_map.mean_confidence < 0.35
    ):
        return TextureHandlingEvidence(
            disposition=TextureHandlingDisposition.MANUAL_REVIEW,
            confidence=max(0.0, min(0.49, region_map.mean_confidence)),
            fail_closed=True,
            reason_codes=["regional_texture_evidence_insufficient"],
            missing_capabilities=[
                "need_region_evidence",
                "need_manual_review",
            ],
            **common,
        )

    try:
        edge_density, local_contrast, local_variation, native_long_edge = (
            _texture_statistics(source_path, preflight, design_spec)
        )
    except (OSError, ValueError) as exc:
        return TextureHandlingEvidence(
            disposition=TextureHandlingDisposition.MANUAL_REVIEW,
            confidence=0.0,
            fail_closed=True,
            reason_codes=[f"texture_statistics_unavailable:{type(exc).__name__}"],
            missing_capabilities=["need_manual_review"],
            **common,
        )

    measured = {
        **common,
        "edge_density": edge_density,
        "local_contrast": local_contrast,
        "local_variation": local_variation,
        "native_long_edge": native_long_edge,
    }

    semantic_reasons: list[str] = []
    if design_spec.perspective_severity > PERSPECTIVE_RISK:
        semantic_reasons.append("perspective_texture_risk")
    if design_spec.occlusion > OCCLUSION_RISK:
        semantic_reasons.append("occlusion_texture_risk")
    if "need_semantic_reconstruction" in design_spec.required_capabilities:
        semantic_reasons.append("semantic_reconstruction_required")

    fine_detail = "fine_detail" in design_spec.texture_classes
    outlined = "outlined" in design_spec.texture_classes
    painterly = "smooth_or_painterly" in design_spec.texture_classes
    source_compression_class = "source_compression" in design_spec.texture_classes

    if fine_detail and (
        preflight.source_quality < FINE_DETAIL_QUALITY_MIN
        or preflight.compression_risk >= FINE_DETAIL_COMPRESSION_MAX
        or source_compression_class
        or native_long_edge < FINE_DETAIL_NATIVE_EDGE_MIN
        or region_map.mean_confidence < REGION_CONFIDENCE_MIN
    ):
        semantic_reasons.append("fine_detail_source_insufficient")

    if semantic_reasons:
        confidence = min(
            0.95,
            max(
                0.50,
                0.55
                + 0.10 * min(1.0, len(semantic_reasons) / 3.0)
                + 0.15 * (1.0 - preflight.source_quality)
                + 0.10 * preflight.compression_risk
                + 0.10 * (1.0 - region_map.mean_confidence),
            ),
        )
        return TextureHandlingEvidence(
            disposition=TextureHandlingDisposition.SEMANTIC_REQUIRED,
            confidence=confidence,
            fail_closed=True,
            reason_codes=sorted(set(semantic_reasons)),
            missing_capabilities=[
                "need_semantic_reconstruction",
                "need_texture_reconstruction",
            ],
            **measured,
        )

    if preflight.source_quality < 0.30:
        return TextureHandlingEvidence(
            disposition=TextureHandlingDisposition.MANUAL_REVIEW,
            confidence=max(0.0, min(0.49, preflight.source_quality)),
            fail_closed=True,
            reason_codes=["texture_source_quality_too_low"],
            missing_capabilities=["need_manual_review", "need_texture_verification"],
            **measured,
        )

    local_candidate = (
        outlined
        and not fine_detail
        and preflight.source_quality >= LOCAL_ENHANCE_QUALITY_MIN
        and preflight.compression_risk < LOCAL_ENHANCE_COMPRESSION_MAX
        and native_long_edge >= LOCAL_ENHANCE_NATIVE_EDGE_MIN
        and region_map.mean_confidence >= REGION_CONFIDENCE_MIN
        and edge_density >= 0.08
        and local_contrast >= 0.12
        and local_variation >= 0.03
    )
    if local_candidate:
        confidence = (
            0.25 * preflight.source_quality
            + 0.20 * (1.0 - preflight.compression_risk)
            + 0.20 * region_map.mean_confidence
            + 0.15 * min(1.0, edge_density / 0.25)
            + 0.10 * local_contrast
            + 0.10 * local_variation
        )
        return TextureHandlingEvidence(
            disposition=TextureHandlingDisposition.LOCAL_DETAIL_ENHANCEMENT_CANDIDATE,
            confidence=max(0.0, min(1.0, confidence)),
            fail_closed=False,
            reason_codes=["outlined_detail_local_enhancement_candidate"],
            **measured,
        )

    reasons: list[str] = []
    if fine_detail:
        reasons.append("fine_detail_preserve_without_unbenchmarked_sharpening")
    if painterly:
        reasons.append("painterly_texture_preserve_raster")
    if outlined:
        reasons.append("outlined_texture_preserve_raster")
    if not reasons:
        reasons.append("unclassified_texture_preserve_raster")

    confidence = (
        0.35 * preflight.source_quality
        + 0.20 * (1.0 - preflight.compression_risk)
        + 0.20 * region_map.mean_confidence
        + 0.15 * local_contrast
        + 0.10 * (1.0 - min(1.0, local_variation))
    )
    return TextureHandlingEvidence(
        disposition=TextureHandlingDisposition.PRESERVE_RASTER,
        confidence=max(0.0, min(1.0, confidence)),
        fail_closed=False,
        reason_codes=sorted(set(reasons)),
        **measured,
    )
