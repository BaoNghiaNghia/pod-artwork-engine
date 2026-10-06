from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from .artwork_detection import crop_artwork
from .contracts import DesignSpec, PreflightResult


@dataclass(frozen=True)
class CandidateInfo:
    path: Path
    source_path: Path
    native_width: int
    native_height: int
    alpha_method: str
    local_baseline: bool


def _border_average(image: Image.Image) -> tuple[float, float, float]:
    rgb = image.convert("RGB")
    w, h = rgb.size
    band = max(1, min(w, h) // 30)
    samples = [
        rgb.crop((0, 0, w, band)),
        rgb.crop((0, max(0, h - band), w, h)),
        rgb.crop((0, 0, band, h)),
        rgb.crop((max(0, w - band), 0, w, h)),
    ]
    pixels: list[tuple[int, int, int]] = []
    for sample in samples:
        thumb = sample.copy()
        thumb.thumbnail((128, 128))
        pixels.extend(thumb.getdata())
    if not pixels:
        return 255.0, 255.0, 255.0
    return (
        sum(pixel[0] for pixel in pixels) / len(pixels),
        sum(pixel[1] for pixel in pixels) / len(pixels),
        sum(pixel[2] for pixel in pixels) / len(pixels),
    )


def _extract_alpha_from_background(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    background = _border_average(rgba)
    pixels = list(rgba.getdata())
    result: list[tuple[int, int, int, int]] = []
    low = 22.0
    high = 78.0
    for r, g, b, original_alpha in pixels:
        distance = math.sqrt(
            (r - background[0]) ** 2
            + (g - background[1]) ** 2
            + (b - background[2]) ** 2
        )
        if distance <= low:
            alpha = 0
        elif distance >= high:
            alpha = original_alpha
        else:
            alpha = round(original_alpha * ((distance - low) / (high - low)))
        if alpha <= 2:
            result.append((0, 0, 0, 0))
        else:
            result.append((r, g, b, alpha))
    rgba.putdata(result)
    return rgba


def _trim(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A")
    box = alpha.point(lambda value: 255 if value >= 5 else 0).getbbox()
    if not box:
        return image
    return image.crop(box)


def _select_primary(
    source_paths: list[Path],
    preflights: list[PreflightResult],
    *,
    preferred_index: int | None = None,
) -> tuple[Path, PreflightResult]:
    if preferred_index is not None:
        if not 0 <= preferred_index < len(preflights):
            raise ValueError("preferred reference index is out of range")
        return source_paths[preferred_index], preflights[preferred_index]

    ranked = sorted(
        zip(source_paths, preflights, strict=True),
        key=lambda item: (
            item[1].artwork_confidence,
            item[1].source_quality,
            item[1].width * item[1].height,
        ),
        reverse=True,
    )
    return ranked[0]


def reconstruct_local_baseline(
    source_paths: list[Path],
    preflights: list[PreflightResult],
    design_spec: DesignSpec,
    output_path: Path,
    *,
    primary_index: int | None = None,
) -> CandidateInfo:
    source_path, preflight = _select_primary(
        source_paths,
        preflights,
        preferred_index=primary_index,
    )
    bbox = design_spec.artwork_bbox or preflight.artwork_bbox
    if bbox is None:
        raise ValueError("artwork bounding box is unavailable")

    crop = crop_artwork(source_path, bbox)
    if preflight.has_alpha and crop.getchannel("A").getextrema() != (255, 255):
        candidate = crop.convert("RGBA")
        alpha_method = "source_alpha"
    else:
        candidate = _extract_alpha_from_background(crop)
        alpha_method = "border_color_soft_mask"

    candidate = _trim(candidate)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    candidate.save(output_path, format="PNG", optimize=True)
    return CandidateInfo(
        path=output_path,
        source_path=source_path,
        native_width=candidate.width,
        native_height=candidate.height,
        alpha_method=alpha_method,
        local_baseline=True,
    )


def normalize_candidate(
    candidate_path: Path,
    output_path: Path,
    *,
    source_path: Path | None = None,
) -> CandidateInfo:
    with Image.open(candidate_path) as source:
        candidate = ImageOps.exif_transpose(source).convert("RGBA")
    candidate = _trim(candidate)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    candidate.save(output_path, format="PNG", optimize=True)
    return CandidateInfo(
        path=output_path,
        source_path=source_path or candidate_path,
        native_width=candidate.width,
        native_height=candidate.height,
        alpha_method="provider_or_existing_alpha",
        local_baseline=False,
    )
