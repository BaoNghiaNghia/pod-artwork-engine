from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from .harness_models import SemanticMetrics, TechnicalMetrics


EVALUATION_LONG_EDGE = 1024
DIFF_LONG_EDGE = 1200


def _load_rgba(path: Path) -> Image.Image:
    with Image.open(path) as source:
        return ImageOps.exif_transpose(source).convert("RGBA").copy()


def _fit_long_edge(image: Image.Image, long_edge: int) -> Image.Image:
    result = image.copy()
    result.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    return result


def _align_pair(
    target_path: Path,
    candidate_path: Path,
    long_edge: int = EVALUATION_LONG_EDGE,
) -> tuple[Image.Image, Image.Image, tuple[int, int], tuple[int, int]]:
    target_full = _load_rgba(target_path)
    candidate_full = _load_rgba(candidate_path)
    target_size = target_full.size
    candidate_size = candidate_full.size

    target = _fit_long_edge(target_full, long_edge)
    candidate = candidate_full.resize(target.size, Image.Resampling.LANCZOS)
    return target, candidate, target_size, candidate_size


def _flatten_white(image: Image.Image) -> Image.Image:
    canvas = Image.new("RGBA", image.size, (255, 255, 255, 255))
    canvas.alpha_composite(image)
    return canvas.convert("RGB")


def _mean_abs_difference(left: Image.Image, right: Image.Image) -> float:
    if left.mode != right.mode:
        right = right.convert(left.mode)
    if left.size != right.size:
        right = right.resize(left.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(left, right)
    histogram = diff.histogram()
    bands = len(diff.getbands())
    pixels = max(1, diff.width * diff.height * bands)
    total = 0
    for index, count in enumerate(histogram):
        value = index % 256
        total += value * count
    return total / pixels / 255.0


def _similarity(left: Image.Image, right: Image.Image) -> float:
    return max(0.0, min(1.0, 1.0 - _mean_abs_difference(left, right)))


def _bbox_iou(left: tuple[int, int, int, int] | None, right: tuple[int, int, int, int] | None) -> float | None:
    if left is None and right is None:
        return 1.0
    if left is None or right is None:
        return 0.0
    lx1, ly1, lx2, ly2 = left
    rx1, ry1, rx2, ry2 = right
    ix1 = max(lx1, rx1)
    iy1 = max(ly1, ry1)
    ix2 = min(lx2, rx2)
    iy2 = min(ly2, ry2)
    intersection = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    left_area = max(0, lx2 - lx1) * max(0, ly2 - ly1)
    right_area = max(0, rx2 - rx1) * max(0, ry2 - ry1)
    union = left_area + right_area - intersection
    return intersection / union if union else 1.0


def _foreground_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    alpha = image.getchannel("A")
    minimum, maximum = alpha.getextrema()
    if minimum == maximum == 255:
        return None
    return alpha.point(lambda value: 255 if value >= 8 else 0).getbbox()


def _edge_image(image: Image.Image) -> Image.Image:
    return _flatten_white(image).convert("L").filter(ImageFilter.FIND_EDGES)


def _edge_energy(edge: Image.Image) -> float:
    histogram = edge.histogram()
    pixels = max(1, edge.width * edge.height)
    return sum(value * count for value, count in enumerate(histogram)) / pixels / 255.0


def _small_detail_survival(target_edge: Image.Image, candidate_edge: Image.Image) -> float | None:
    target_values = list(target_edge.getdata())
    candidate_values = list(candidate_edge.getdata())
    strong = [index for index, value in enumerate(target_values) if value >= 72]
    if not strong:
        return None
    survived = sum(1 for index in strong if candidate_values[index] >= 48)
    return survived / len(strong)


def _boundary_mask(alpha: Image.Image) -> Image.Image | None:
    minimum, maximum = alpha.getextrema()
    if minimum == maximum:
        return None
    binary = alpha.point(lambda value: 255 if value >= 128 else 0)
    boundary = binary.filter(ImageFilter.FIND_EDGES)
    if boundary.getbbox() is None:
        return None
    return boundary


def _masked_similarity(left: Image.Image, right: Image.Image, mask: Image.Image) -> float | None:
    if left.size != right.size:
        right = right.resize(left.size, Image.Resampling.LANCZOS)
    if mask.size != left.size:
        mask = mask.resize(left.size, Image.Resampling.NEAREST)

    diff = ImageChops.difference(left.convert("RGB"), right.convert("RGB")).convert("L")
    values = list(diff.getdata())
    mask_values = list(mask.getdata())
    selected = [value for value, include in zip(values, mask_values, strict=True) if include >= 32]
    if not selected:
        return None
    error = sum(selected) / len(selected) / 255.0
    return max(0.0, min(1.0, 1.0 - error))


def _normalize_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip().casefold()
    return value


def exact_text_score(expected: list[str], recognized: list[str]) -> float | None:
    if not expected or not recognized:
        return None
    expected_normalized = [_normalize_text(value) for value in expected]
    recognized_normalized = [_normalize_text(value) for value in recognized]
    return 1.0 if expected_normalized == recognized_normalized else 0.0


def evaluate_images(
    target_path: Path,
    candidate_path: Path,
    *,
    expected_text: list[str] | None = None,
    recognized_text: list[str] | None = None,
) -> tuple[SemanticMetrics, TechnicalMetrics]:
    target, candidate, target_size, candidate_size = _align_pair(target_path, candidate_path)

    target_rgb = _flatten_white(target)
    candidate_rgb = _flatten_white(candidate)
    target_edge = _edge_image(target)
    candidate_edge = _edge_image(candidate)

    layout = _bbox_iou(_foreground_bbox(target), _foreground_bbox(candidate))
    color = _similarity(target_rgb, candidate_rgb)
    edge = _similarity(target_edge, candidate_edge)

    target_alpha = target.getchannel("A")
    candidate_alpha = candidate.getchannel("A")
    target_alpha_range = target_alpha.getextrema()
    candidate_alpha_range = candidate_alpha.getextrema()
    alpha = None
    if target_alpha_range != (255, 255) or candidate_alpha_range != (255, 255):
        alpha = _similarity(target_alpha, candidate_alpha)

    boundary = _boundary_mask(target_alpha)
    halo_aliasing = (
        _masked_similarity(target_rgb, candidate_rgb, boundary)
        if boundary is not None
        else None
    )

    target_energy = _edge_energy(target_edge)
    candidate_energy = _edge_energy(candidate_edge)
    if target_energy <= 1e-9 and candidate_energy <= 1e-9:
        blur = 1.0
    elif target_energy <= 1e-9 or candidate_energy <= 1e-9:
        blur = 0.0
    else:
        blur = min(target_energy, candidate_energy) / max(target_energy, candidate_energy)

    width_ratio = candidate_size[0] / max(target_size[0], 1)
    height_ratio = candidate_size[1] / max(target_size[1], 1)
    effective_resolution = max(0.0, min(1.0, width_ratio, height_ratio))
    detail = _small_detail_survival(target_edge, candidate_edge)

    semantic = SemanticMetrics(
        exact_text=exact_text_score(expected_text or [], recognized_text or []),
        layout=layout,
        object_fidelity=None,
        color=color,
        texture=edge,
        missing_detail=detail,
    )
    technical = TechnicalMetrics(
        edge=edge,
        alpha=alpha,
        halo_aliasing=halo_aliasing,
        blur=blur,
        effective_resolution=effective_resolution,
        small_detail_survival=detail,
    )
    return semantic, technical


def _available(values: list[float | None]) -> list[float]:
    return [value for value in values if value is not None]


def aggregate_metric_scores(
    semantic: SemanticMetrics,
    technical: TechnicalMetrics,
) -> tuple[float | None, float | None, float | None]:
    semantic_values = _available(
        [
            semantic.exact_text,
            semantic.layout,
            semantic.object_fidelity,
            semantic.color,
            semantic.texture,
            semantic.missing_detail,
        ]
    )
    technical_values = _available(
        [
            technical.edge,
            technical.alpha,
            technical.halo_aliasing,
            technical.blur,
            technical.effective_resolution,
            technical.small_detail_survival,
        ]
    )
    semantic_score = sum(semantic_values) / len(semantic_values) if semantic_values else None
    technical_score = sum(technical_values) / len(technical_values) if technical_values else None

    if semantic_score is not None and technical_score is not None:
        quality = 0.6 * semantic_score + 0.4 * technical_score
    elif semantic_score is not None:
        quality = semantic_score
    else:
        quality = technical_score
    return semantic_score, technical_score, quality


def write_visual_diff(
    target_path: Path,
    candidate_path: Path,
    output_path: Path,
) -> Path:
    target_full = _load_rgba(target_path)
    candidate_full = _load_rgba(candidate_path)

    target = _fit_long_edge(target_full, DIFF_LONG_EDGE)
    candidate = candidate_full.resize(target.size, Image.Resampling.LANCZOS)
    target_rgb = _flatten_white(target)
    candidate_rgb = _flatten_white(candidate)

    raw_diff = ImageChops.difference(target_rgb, candidate_rgb)
    heatmap = ImageEnhance.Contrast(raw_diff.convert("L")).enhance(3.0).convert("RGB")

    header = 32
    panel_width, panel_height = target.size
    canvas = Image.new(
        "RGB",
        (panel_width * 3, panel_height + header),
        "white",
    )
    canvas.paste(target_rgb, (0, header))
    canvas.paste(candidate_rgb, (panel_width, header))
    canvas.paste(heatmap, (panel_width * 2, header))

    draw = ImageDraw.Draw(canvas)
    draw.text((8, 8), "TARGET", fill="black")
    draw.text((panel_width + 8, 8), "RESULT", fill="black")
    draw.text((panel_width * 2 + 8, 8), "DIFF", fill="black")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)
    return output_path
