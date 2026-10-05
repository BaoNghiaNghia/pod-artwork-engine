from __future__ import annotations

from collections import Counter
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from .artwork_detection import crop_artwork
from .contracts import ArtworkType, DesignSpec, PreflightResult


def _dominant_colors(path: Path, preflight: PreflightResult, limit: int = 6) -> list[str]:
    if preflight.artwork_bbox is None:
        return []
    crop = crop_artwork(path, preflight.artwork_bbox).convert("RGB")
    crop.thumbnail((256, 256), Image.Resampling.LANCZOS)
    quantized = crop.quantize(colors=limit, method=Image.Quantize.MEDIANCUT).convert("RGB")
    counts = Counter(quantized.getdata())
    return [
        f"#{r:02x}{g:02x}{b:02x}"
        for (r, g, b), _ in counts.most_common(limit)
    ]


def _edge_density(path: Path, preflight: PreflightResult) -> float:
    if preflight.artwork_bbox is None:
        return 0.0
    crop = crop_artwork(path, preflight.artwork_bbox).convert("L")
    crop.thumbnail((512, 512), Image.Resampling.LANCZOS)
    edges = crop.filter(ImageFilter.FIND_EDGES)
    values = list(edges.getdata())
    if not values:
        return 0.0
    return sum(value >= 48 for value in values) / len(values)


def _estimate_artwork_type(
    path: Path,
    preflight: PreflightResult,
    dominant_colors: list[str],
) -> ArtworkType:
    density = _edge_density(path, preflight)
    if preflight.has_alpha and len(dominant_colors) <= 4 and density > 0.12:
        return ArtworkType.LOGO
    if len(dominant_colors) <= 3 and density > 0.16:
        return ArtworkType.LOGO
    if density > 0.28:
        return ArtworkType.MIXED
    return ArtworkType.ILLUSTRATION


def _texture_classes(path: Path, preflight: PreflightResult) -> list[str]:
    density = _edge_density(path, preflight)
    classes: list[str] = []
    if density > 0.30:
        classes.append("fine_detail")
    elif density > 0.18:
        classes.append("outlined")
    else:
        classes.append("smooth_or_painterly")
    if preflight.compression_risk >= 0.55:
        classes.append("source_compression")
    return classes


def analyze_locally(
    source_paths: list[Path],
    preflights: list[PreflightResult],
) -> DesignSpec:
    if not source_paths or not preflights or len(source_paths) != len(preflights):
        raise ValueError("local analysis requires matching source paths and preflight results")

    ranked = sorted(
        zip(source_paths, preflights, strict=True),
        key=lambda item: (
            item[1].artwork_confidence,
            item[1].source_quality,
            item[1].width * item[1].height,
        ),
        reverse=True,
    )
    primary_path, primary = ranked[0]
    colors = _dominant_colors(primary_path, primary)
    artwork_type = _estimate_artwork_type(primary_path, primary, colors)
    texture = _texture_classes(primary_path, primary)

    confidence = (
        0.55 * primary.artwork_confidence
        + 0.45 * primary.source_quality
    )
    capabilities: list[str] = []
    if artwork_type in {ArtworkType.LOGO, ArtworkType.TYPOGRAPHY}:
        capabilities.extend(["need_exact_text", "need_vector"])
    elif not (primary.has_alpha and primary.artwork_confidence >= 0.70):
        capabilities.append("need_semantic_reconstruction")
    if primary.artwork_confidence < 0.35:
        capabilities.append("need_semantic_reconstruction")
    if "fine_detail" in texture:
        capabilities.append("need_complex_texture")

    return DesignSpec(
        artwork_type=artwork_type,
        artwork_bbox=primary.artwork_bbox,
        dominant_colors=colors,
        texture_classes=texture,
        perspective_severity=0.0,
        occlusion=max(0.0, min(1.0, 1.0 - primary.artwork_confidence)),
        confidence=max(0.0, min(1.0, confidence)),
        required_capabilities=sorted(set(capabilities)),
    )
