from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

from .artwork_detection import crop_artwork
from .contracts import (
    BoundingBox,
    FontMatchEvidence,
    TypographyLine,
    TypographySpec,
)
from .font_catalog import FontEntry, get_font_catalog, normalize_font_name
from .settings import Settings


MATCH_METHOD = "visual_render_compare_v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class _ScoredFont:
    entry: FontEntry
    score: float
    aspect_score: float


def _clamp_box(
    bbox: BoundingBox,
    width: int,
    height: int,
    *,
    pad_ratio: float = 0.12,
) -> tuple[int, int, int, int]:
    pad_x = bbox.width * pad_ratio
    pad_y = bbox.height * pad_ratio
    left = max(0, min(width - 1, round((bbox.x - pad_x) * width)))
    top = max(0, min(height - 1, round((bbox.y - pad_y) * height)))
    right = max(
        left + 1,
        min(width, round((bbox.x + bbox.width + pad_x) * width)),
    )
    bottom = max(
        top + 1,
        min(height, round((bbox.y + bbox.height + pad_y) * height)),
    )
    return left, top, right, bottom


def _otsu_threshold(image: Image.Image) -> int:
    histogram = image.histogram()
    total = sum(histogram)
    if total <= 0:
        return 128

    weighted_sum = sum(index * count for index, count in enumerate(histogram))
    background_weight = 0
    background_sum = 0.0
    best_variance = -1.0
    best_threshold = 128

    for threshold, count in enumerate(histogram):
        background_weight += count
        if background_weight == 0:
            continue
        foreground_weight = total - background_weight
        if foreground_weight == 0:
            break
        background_sum += threshold * count
        background_mean = background_sum / background_weight
        foreground_mean = (
            weighted_sum - background_sum
        ) / foreground_weight
        variance = (
            background_weight
            * foreground_weight
            * (background_mean - foreground_mean) ** 2
        )
        if variance > best_variance:
            best_variance = variance
            best_threshold = threshold
    return best_threshold


def _sample_border_background(image: Image.Image) -> tuple[int, int, int]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    if width <= 1 or height <= 1:
        pixel = rgb.getpixel((0, 0))
        return int(pixel[0]), int(pixel[1]), int(pixel[2])

    stride = max(1, (width + height) // 300)
    samples: list[tuple[int, int, int]] = []
    for x in range(0, width, stride):
        samples.append(rgb.getpixel((x, 0)))
        samples.append(rgb.getpixel((x, height - 1)))
    for y in range(0, height, stride):
        samples.append(rgb.getpixel((0, y)))
        samples.append(rgb.getpixel((width - 1, y)))

    if not samples:
        samples.append(rgb.getpixel((0, 0)))

    return (
        int(statistics.median(pixel[0] for pixel in samples)),
        int(statistics.median(pixel[1] for pixel in samples)),
        int(statistics.median(pixel[2] for pixel in samples)),
    )


def _foreground_mask(image: Image.Image) -> Image.Image | None:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    alpha_extrema = alpha.getextrema()
    transparent_ratio = sum(
        1 for value in alpha.resize((64, 64)).getdata() if value < 245
    ) / (64 * 64)

    if alpha_extrema[0] < 245 and transparent_ratio >= 0.08:
        signal = ImageOps.autocontrast(alpha)
    else:
        background = _sample_border_background(rgba)
        rgb = rgba.convert("RGB")
        solid = Image.new("RGB", rgb.size, background)
        diff = ImageChops.difference(rgb, solid)
        red, green, blue = diff.split()
        signal = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        signal = ImageOps.autocontrast(signal)

    threshold = max(8, _otsu_threshold(signal))
    mask = signal.point(lambda value: 255 if value > threshold else 0, mode="L")

    box = mask.getbbox()
    if box is None:
        return None

    cropped = mask.crop(box)
    if cropped.width < 2 or cropped.height < 2:
        return None
    return cropped


def _normalized_mask(mask: Image.Image, size: tuple[int, int] = (320, 112)) -> Image.Image:
    result = Image.new("L", size, 0)
    contained = ImageOps.contain(mask, size, Image.Resampling.LANCZOS)
    left = (size[0] - contained.width) // 2
    top = (size[1] - contained.height) // 2
    result.paste(contained, (left, top))
    return result.filter(ImageFilter.GaussianBlur(radius=0.55))


def _font_aspect(entry: FontEntry, text: str) -> float | None:
    try:
        font = ImageFont.truetype(str(entry.path), size=64)
        bbox = font.getbbox(text)
    except Exception:
        return None
    width = max(1, bbox[2] - bbox[0])
    height = max(1, bbox[3] - bbox[1])
    if width <= 1 or height <= 1:
        return None
    return width / height


def _render_font_mask(entry: FontEntry, text: str) -> tuple[Image.Image, float] | None:
    try:
        font = ImageFont.truetype(str(entry.path), size=128)
    except Exception:
        return None

    probe = Image.new("L", (8, 8), 0)
    draw = ImageDraw.Draw(probe)
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
    except Exception:
        return None

    width = max(1, bbox[2] - bbox[0])
    height = max(1, bbox[3] - bbox[1])
    if width <= 1 or height <= 1:
        return None

    margin = 10
    canvas = Image.new("L", (width + margin * 2, height + margin * 2), 0)
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (margin - bbox[0], margin - bbox[1]),
        text,
        font=font,
        fill=255,
    )
    box = canvas.getbbox()
    if box is None:
        return None
    mask = canvas.crop(box)
    return mask, mask.width / max(1, mask.height)


def _shape_score(
    observed: Image.Image,
    observed_aspect: float,
    candidate: Image.Image,
    candidate_aspect: float,
) -> tuple[float, float]:
    observed_norm = _normalized_mask(observed)
    candidate_norm = _normalized_mask(candidate)
    diff = ImageChops.difference(observed_norm, candidate_norm)
    mean_difference = ImageStat.Stat(diff).mean[0] / 255.0
    shape_score = max(0.0, min(1.0, 1.0 - mean_difference))

    ratio = max(1e-6, candidate_aspect / max(1e-6, observed_aspect))
    aspect_score = math.exp(-1.6 * abs(math.log(ratio)))
    score = 0.78 * shape_score + 0.22 * aspect_score
    return max(0.0, min(1.0, score)), aspect_score


def _deduplicated_entries(entries: list[FontEntry]) -> list[FontEntry]:
    unique: dict[tuple[str, str, int], FontEntry] = {}
    for entry in entries:
        key = (
            entry.normalized_family,
            normalize_font_name(entry.style),
            entry.inferred_weight,
        )
        current = unique.get(key)
        if current is None or str(entry.path) < str(current.path):
            unique[key] = entry
    return sorted(
        unique.values(),
        key=lambda item: (
            item.normalized_family,
            normalize_font_name(item.style),
            item.inferred_weight,
            str(item.path),
        ),
    )


def _prefilter_entries(
    entries: list[FontEntry],
    text: str,
    observed_aspect: float,
    max_candidates: int,
) -> list[tuple[FontEntry, Image.Image, float]]:
    ranked: list[tuple[float, str, FontEntry]] = []
    for entry in _deduplicated_entries(entries):
        aspect = _font_aspect(entry, text)
        if aspect is None:
            continue
        ratio = max(1e-6, aspect / max(1e-6, observed_aspect))
        ranked.append(
            (
                abs(math.log(ratio)),
                f"{entry.normalized_family}:{entry.inferred_weight}:{entry.style}",
                entry,
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1]))

    candidates: list[tuple[FontEntry, Image.Image, float]] = []
    for _, _, entry in ranked[: max(8, max_candidates)]:
        rendered = _render_font_mask(entry, text)
        if rendered is None:
            continue
        mask, aspect = rendered
        candidates.append((entry, mask, aspect))
    return candidates


def _match_line(
    artwork: Image.Image,
    line: TypographyLine,
    entries: list[FontEntry],
    settings: Settings,
) -> TypographyLine:
    if line.font_match is not None and line.font_match.accepted:
        return line
    if len("".join(character for character in line.text if character.isalnum())) < 2:
        return line.model_copy(
            update={
                "font_match": FontMatchEvidence(
                    method=MATCH_METHOD,
                    candidates_evaluated=0,
                )
            }
        )

    crop_box = _clamp_box(line.bbox, artwork.width, artwork.height)
    observed_crop = artwork.crop(crop_box)
    observed = _foreground_mask(observed_crop)
    if observed is None:
        return line.model_copy(
            update={
                "font_match": FontMatchEvidence(
                    method=MATCH_METHOD,
                    candidates_evaluated=0,
                )
            }
        )

    observed_aspect = observed.width / max(1, observed.height)
    candidates = _prefilter_entries(
        entries,
        line.text,
        observed_aspect,
        settings.visual_font_match_max_candidates,
    )
    scored: list[_ScoredFont] = []
    for entry, candidate_mask, candidate_aspect in candidates:
        score, aspect_score = _shape_score(
            observed,
            observed_aspect,
            candidate_mask,
            candidate_aspect,
        )
        scored.append(
            _ScoredFont(
                entry=entry,
                score=score,
                aspect_score=aspect_score,
            )
        )

    if not scored:
        return line.model_copy(
            update={
                "font_match": FontMatchEvidence(
                    method=MATCH_METHOD,
                    candidates_evaluated=0,
                )
            }
        )

    scored.sort(
        key=lambda item: (
            -item.score,
            -item.aspect_score,
            item.entry.normalized_family,
            item.entry.inferred_weight,
            str(item.entry.path),
        )
    )
    best = scored[0]
    runner_up_score = scored[1].score if len(scored) > 1 else 0.0
    margin = max(0.0, best.score - runner_up_score)
    accepted = (
        best.score >= settings.visual_font_match_min_score
        and margin >= settings.visual_font_match_min_margin
    )
    font_sha256 = ""
    if accepted:
        try:
            font_sha256 = _sha256_file(best.entry.path)
        except OSError:
            accepted = False

    evidence = FontMatchEvidence(
        family=best.entry.family,
        style=best.entry.style,
        weight=best.entry.inferred_weight,
        score=best.score,
        margin=margin,
        accepted=accepted,
        method=MATCH_METHOD,
        candidates_evaluated=len(scored),
        font_sha256=font_sha256,
    )

    updates: dict[str, object] = {"font_match": evidence}
    if accepted:
        updates.update(
            {
                "font_family": best.entry.family,
                "font_weight": best.entry.inferred_weight,
            }
        )
    return line.model_copy(update=updates)


def match_typography_fonts(
    source_path: Path,
    artwork_bbox: BoundingBox,
    typography: TypographySpec,
    settings: Settings,
) -> TypographySpec:
    if not settings.visual_font_match_enabled or not typography.lines:
        return typography

    catalog = get_font_catalog(settings)
    if not catalog.entries:
        return typography

    artwork = crop_artwork(source_path, artwork_bbox).convert("RGBA")
    artwork.thumbnail((2400, 2400), Image.Resampling.LANCZOS)

    lines = [
        _match_line(artwork, line, catalog.entries, settings)
        for line in typography.lines
    ]
    accepted = [
        line.font_match
        for line in lines
        if line.font_match is not None and line.font_match.accepted
    ]
    all_accepted = len(accepted) == len(lines) and bool(lines)
    aggregate_confidence = (
        min(match.score for match in accepted)
        if all_accepted and accepted
        else 0.0
    )

    provider = typography.evidence_provider
    if (
        any(line.font_match is not None for line in lines)
        and MATCH_METHOD not in provider
    ):
        provider = f"{provider}+{MATCH_METHOD}" if provider else MATCH_METHOD

    return typography.model_copy(
        update={
            "lines": lines,
            "font_match_confidence": aggregate_confidence,
            "evidence_provider": provider,
        }
    )


def merge_verified_font_matches(
    typography: TypographySpec,
    verified: TypographySpec | None,
) -> TypographySpec:
    if verified is None or not verified.lines or not typography.lines:
        return typography

    by_text: dict[str, list[TypographyLine]] = {}
    for line in verified.lines:
        if line.font_match is None or not line.font_match.accepted:
            continue
        by_text.setdefault(line.text.strip().casefold(), []).append(line)

    merged: list[TypographyLine] = []
    for line in typography.lines:
        key = line.text.strip().casefold()
        candidates = by_text.get(key) or []
        verified_line = candidates.pop(0) if candidates else None
        if verified_line is None:
            merged.append(line)
            continue
        merged.append(
            line.model_copy(
                update={
                    "font_family": verified_line.font_family,
                    "font_weight": verified_line.font_weight,
                    "font_match": verified_line.font_match,
                }
            )
        )

    accepted = [
        line.font_match
        for line in merged
        if line.font_match is not None and line.font_match.accepted
    ]
    visual_confidence = (
        min(match.score for match in accepted)
        if len(accepted) == len(merged) and merged
        else 0.0
    )
    return typography.model_copy(
        update={
            "lines": merged,
            "font_match_confidence": max(
                typography.font_match_confidence,
                visual_confidence,
            ),
            "evidence_provider": (
                f"{typography.evidence_provider}+{MATCH_METHOD}"
                if MATCH_METHOD not in typography.evidence_provider
                and typography.evidence_provider
                else typography.evidence_provider or MATCH_METHOD
            ),
        }
    )
