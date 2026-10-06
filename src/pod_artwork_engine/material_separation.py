from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageOps, ImageStat

from .artwork_detection import crop_artwork
from .contracts import (
    ArtworkType,
    BoundingBox,
    DesignSpec,
    MaterialSeparationDisposition,
    MaterialSeparationEvidence,
    PreflightResult,
)


BORDER_UNIFORMITY_MIN = 0.90
FOREGROUND_CONTRAST_MIN = 0.10
ARTWORK_CONFIDENCE_MIN = 0.50
EDGE_MARGIN = 0.02
MEANINGFUL_TRANSPARENCY_MIN = 0.005
VISIBLE_ALPHA_MIN = 0.01
SEMANTIC_RISK_THRESHOLD = 0.10


def _alpha_statistics(image: Image.Image) -> tuple[bool, float, float]:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    working = alpha.copy()
    working.thumbnail((512, 512), Image.Resampling.LANCZOS)
    values = list(working.getdata())
    if not values:
        return False, 0.0, 0.0

    total = len(values)
    transparent_fraction = sum(value < 250 for value in values) / total
    visible_fraction = sum(value >= 12 for value in values) / total
    meaningful = (
        transparent_fraction >= MEANINGFUL_TRANSPARENCY_MIN
        and visible_fraction >= VISIBLE_ALPHA_MIN
    )
    return meaningful, transparent_fraction, visible_fraction


def _border_pixels(image: Image.Image) -> list[tuple[int, int, int]]:
    rgb = image.convert("RGB")
    working = rgb.copy()
    working.thumbnail((512, 512), Image.Resampling.LANCZOS)
    width, height = working.size
    band = max(1, min(width, height) // 30)
    strips = [
        working.crop((0, 0, width, band)),
        working.crop((0, max(0, height - band), width, height)),
        working.crop((0, 0, band, height)),
        working.crop((max(0, width - band), 0, width, height)),
    ]

    pixels: list[tuple[int, int, int]] = []
    for strip in strips:
        sample = strip.copy()
        sample.thumbnail((256, 64), Image.Resampling.LANCZOS)
        pixels.extend(sample.getdata())
    return pixels


def _border_statistics(
    image: Image.Image,
) -> tuple[tuple[float, float, float], float]:
    pixels = _border_pixels(image)
    if not pixels:
        return (255.0, 255.0, 255.0), 0.0

    sample = Image.new("RGB", (len(pixels), 1))
    sample.putdata(pixels)
    stat = ImageStat.Stat(sample)
    mean = tuple(float(value) for value in stat.mean[:3])
    spread = math.sqrt(
        sum(float(value) ** 2 for value in stat.stddev[:3]) / 3.0
    )
    uniformity = 1.0 - min(1.0, spread / 48.0)
    return (mean[0], mean[1], mean[2]), max(0.0, min(1.0, uniformity))


def _edge_contact_ratio(bbox: BoundingBox) -> float:
    contacts = sum(
        (
            bbox.x <= EDGE_MARGIN,
            bbox.y <= EDGE_MARGIN,
            bbox.x + bbox.width >= 1.0 - EDGE_MARGIN,
            bbox.y + bbox.height >= 1.0 - EDGE_MARGIN,
        )
    )
    return contacts / 4.0


def _foreground_contrast(
    source_path: Path,
    bbox: BoundingBox,
    border_mean: tuple[float, float, float],
) -> float:
    crop = crop_artwork(source_path, bbox).convert("RGB")
    working = crop.copy()
    working.thumbnail((384, 384), Image.Resampling.LANCZOS)
    mean = ImageStat.Stat(working).mean[:3]
    distance = math.sqrt(
        sum(
            (float(mean[channel]) - border_mean[channel]) ** 2
            for channel in range(3)
        )
    )
    return max(
        0.0,
        min(1.0, distance / (math.sqrt(3.0) * 255.0)),
    )


def _semantic_risk_reasons(design_spec: DesignSpec) -> list[str]:
    reasons: list[str] = []
    if design_spec.artwork_type in {ArtworkType.ILLUSTRATION, ArtworkType.MIXED}:
        reasons.append("illustration_or_mixed_material")
    if "fine_detail" in design_spec.texture_classes:
        reasons.append("fine_detail_edges")
    if "source_compression" in design_spec.texture_classes:
        reasons.append("source_compression_risk")
    if design_spec.perspective_severity > SEMANTIC_RISK_THRESHOLD:
        reasons.append("perspective_separation_risk")
    if design_spec.occlusion > SEMANTIC_RISK_THRESHOLD:
        reasons.append("occlusion_separation_risk")
    if "need_semantic_reconstruction" in design_spec.required_capabilities:
        reasons.append("semantic_reconstruction_required")
    return reasons


def build_material_separation_evidence(
    source_path: Path,
    preflight: PreflightResult,
    design_spec: DesignSpec,
    *,
    primary_index: int,
) -> MaterialSeparationEvidence:
    bbox = design_spec.artwork_bbox or preflight.artwork_bbox
    if bbox is None:
        return MaterialSeparationEvidence(
            disposition=MaterialSeparationDisposition.MANUAL_REVIEW,
            primary_index=primary_index,
            confidence=0.0,
            fail_closed=True,
            source_has_alpha=preflight.has_alpha,
            reason_codes=["artwork_bbox_unavailable"],
            missing_capabilities=["need_artwork_localization", "need_manual_review"],
        )

    try:
        with Image.open(source_path) as source:
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGBA")
    except OSError as exc:
        return MaterialSeparationEvidence(
            disposition=MaterialSeparationDisposition.MANUAL_REVIEW,
            primary_index=primary_index,
            confidence=0.0,
            fail_closed=True,
            artwork_bbox=bbox,
            source_has_alpha=preflight.has_alpha,
            reason_codes=[f"source_image_unavailable:{type(exc).__name__}"],
            missing_capabilities=["need_manual_review"],
        )

    meaningful_alpha, transparent_fraction, visible_fraction = _alpha_statistics(image)
    border_mean, border_uniformity = _border_statistics(image)
    edge_contact_ratio = _edge_contact_ratio(bbox)
    foreground_contrast = _foreground_contrast(source_path, bbox, border_mean)

    common = {
        "primary_index": primary_index,
        "artwork_bbox": bbox,
        "source_has_alpha": preflight.has_alpha,
        "meaningful_alpha": meaningful_alpha,
        "transparent_fraction": transparent_fraction,
        "visible_fraction": visible_fraction,
        "border_uniformity": border_uniformity,
        "edge_contact_ratio": edge_contact_ratio,
        "foreground_contrast": foreground_contrast,
    }

    if meaningful_alpha:
        confidence = min(
            0.99,
            0.72
            + 0.18 * min(1.0, transparent_fraction / 0.20)
            + 0.09 * min(1.0, preflight.artwork_confidence),
        )
        reasons = ["meaningful_source_alpha"]
        if edge_contact_ratio > 0:
            reasons.append("alpha_foreground_touches_source_edge")
            confidence = min(confidence, 0.88)
        return MaterialSeparationEvidence(
            disposition=MaterialSeparationDisposition.EXISTING_ALPHA,
            confidence=max(0.0, min(1.0, confidence)),
            fail_closed=False,
            reason_codes=reasons,
            **common,
        )

    weak_reasons: list[str] = []
    if preflight.artwork_confidence < ARTWORK_CONFIDENCE_MIN:
        weak_reasons.append("artwork_localization_low_confidence")
    if preflight.source_quality < 0.25:
        weak_reasons.append("source_quality_too_low")
    if foreground_contrast < 0.04:
        weak_reasons.append("foreground_background_contrast_too_low")

    if weak_reasons:
        if preflight.has_alpha:
            weak_reasons.append("declared_alpha_not_meaningful")
        return MaterialSeparationEvidence(
            disposition=MaterialSeparationDisposition.MANUAL_REVIEW,
            confidence=max(
                0.0,
                min(
                    0.49,
                    0.45 * preflight.artwork_confidence
                    + 0.35 * preflight.source_quality
                    + 0.20 * foreground_contrast,
                ),
            ),
            fail_closed=True,
            reason_codes=sorted(set(weak_reasons)),
            missing_capabilities=[
                "need_material_separation_verification",
                "need_manual_review",
            ],
            **common,
        )

    semantic_reasons = _semantic_risk_reasons(design_spec)
    if border_uniformity < BORDER_UNIFORMITY_MIN:
        semantic_reasons.append("nonuniform_background")
    if edge_contact_ratio > 0:
        semantic_reasons.append("artwork_touches_source_edge")
    if preflight.compression_risk >= 0.65:
        semantic_reasons.append("compression_separation_risk")

    simple_candidate = (
        not semantic_reasons
        and border_uniformity >= BORDER_UNIFORMITY_MIN
        and foreground_contrast >= FOREGROUND_CONTRAST_MIN
        and preflight.artwork_confidence >= ARTWORK_CONFIDENCE_MIN
    )
    if simple_candidate:
        confidence = (
            0.40 * border_uniformity
            + 0.25 * min(1.0, foreground_contrast / 0.35)
            + 0.20 * preflight.artwork_confidence
            + 0.15 * preflight.source_quality
        )
        reasons = ["uniform_border_background_candidate"]
        if preflight.has_alpha:
            reasons.append("declared_alpha_not_meaningful")
        return MaterialSeparationEvidence(
            disposition=MaterialSeparationDisposition.SIMPLE_BORDER_BACKGROUND,
            confidence=max(0.0, min(1.0, confidence)),
            fail_closed=False,
            reason_codes=reasons,
            **common,
        )

    if semantic_reasons:
        confidence = max(
            0.50,
            min(
                0.95,
                0.55
                + 0.20 * (1.0 - border_uniformity)
                + 0.15 * edge_contact_ratio
                + 0.10 * min(1.0, len(semantic_reasons) / 4.0),
            ),
        )
        return MaterialSeparationEvidence(
            disposition=MaterialSeparationDisposition.SEMANTIC_REQUIRED,
            confidence=confidence,
            fail_closed=True,
            reason_codes=sorted(set(semantic_reasons)),
            missing_capabilities=[
                "need_material_separation",
                "need_semantic_reconstruction",
            ],
            **common,
        )

    return MaterialSeparationEvidence(
        disposition=MaterialSeparationDisposition.MANUAL_REVIEW,
        confidence=max(
            0.0,
            min(
                0.59,
                0.35 * border_uniformity
                + 0.30 * foreground_contrast
                + 0.20 * preflight.artwork_confidence
                + 0.15 * preflight.source_quality,
            ),
        ),
        fail_closed=True,
        reason_codes=["material_separation_evidence_inconclusive"],
        missing_capabilities=[
            "need_material_separation_verification",
            "need_manual_review",
        ],
        **common,
    )
