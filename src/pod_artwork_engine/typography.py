from __future__ import annotations

import os
import re
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .contracts import RegionReplacementMode, TypographyLine, TypographySpec
from .settings import Settings


class TypographyRenderUnavailable(RuntimeError):
    pass


def _normalize_font_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _font_candidates(settings: Settings) -> list[Path]:
    roots = [settings.fonts_dir]
    if os.name == "nt":
        windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
        roots.append(windows / "Fonts")

    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in ("*.ttf", "*.otf", "*.ttc"):
            candidates.extend(root.glob(pattern))
    return sorted(set(path.resolve() for path in candidates))


def resolve_font(settings: Settings, family: str, weight: int = 400) -> Path | None:
    wanted = _normalize_font_name(family)
    if not wanted:
        return None

    weighted_terms: list[str] = []
    if weight >= 700:
        weighted_terms = ["bold", "bd", "semibold", "demi"]
    elif weight <= 300:
        weighted_terms = ["light", "thin", "lt"]

    ranked: list[tuple[int, int, int, str, Path]] = []
    for path in _font_candidates(settings):
        stem = _normalize_font_name(path.stem)
        if wanted not in stem and stem not in wanted:
            continue
        exact = 0 if stem == wanted else 1
        weight_penalty = 0
        if weighted_terms and not any(term in stem for term in weighted_terms):
            weight_penalty = 1
        containment_penalty = abs(len(stem) - len(wanted))
        ranked.append((exact, weight_penalty, containment_penalty, str(path), path))

    if not ranked:
        return None
    ranked.sort(key=lambda item: item[:4])
    return ranked[0][4]


def _parse_color(value: str) -> tuple[int, int, int, int]:
    try:
        parsed = ImageColor.getcolor(value, "RGBA")
    except ValueError as exc:
        raise TypographyRenderUnavailable(f"invalid typography color: {value}") from exc
    return parsed


def _line_box_pixels(
    line: TypographyLine,
    canvas: Image.Image,
) -> tuple[int, int, int, int]:
    left = max(0, min(canvas.width - 1, round(line.bbox.x * canvas.width)))
    top = max(0, min(canvas.height - 1, round(line.bbox.y * canvas.height)))
    right = max(
        left + 1,
        min(canvas.width, round((line.bbox.x + line.bbox.width) * canvas.width)),
    )
    bottom = max(
        top + 1,
        min(canvas.height, round((line.bbox.y + line.bbox.height) * canvas.height)),
    )
    return left, top, right, bottom


def _fit_font(
    font_path: Path,
    text: str,
    width: int,
    height: int,
    *,
    stroke_width: int = 0,
) -> ImageFont.FreeTypeFont:
    low = 6
    high = max(7, int(height * 1.8))
    best: ImageFont.FreeTypeFont | None = None
    probe = Image.new("L", (4, 4), 0)
    draw = ImageDraw.Draw(probe)

    while low <= high:
        size = (low + high) // 2
        font = ImageFont.truetype(str(font_path), size=size)
        box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        text_width = max(1, box[2] - box[0])
        text_height = max(1, box[3] - box[1])
        if text_width <= width and text_height <= height:
            best = font
            low = size + 1
        else:
            high = size - 1

    if best is None:
        raise TypographyRenderUnavailable("text cannot fit the requested typography box")
    return best


def _draw_line(
    canvas: Image.Image,
    line: TypographyLine,
    settings: Settings,
) -> None:
    font_path = resolve_font(settings, line.font_family, line.font_weight)
    if font_path is None:
        raise TypographyRenderUnavailable(
            f"required font is not installed: {line.font_family or '<unspecified>'}"
        )

    left, top, right, bottom = _line_box_pixels(line, canvas)
    width = right - left
    height = bottom - top
    stroke_width = max(0, round(line.stroke_width_ratio * max(width, height)))

    font = _fit_font(
        font_path,
        line.text,
        width,
        height,
        stroke_width=stroke_width,
    )

    layer_margin = max(8, stroke_width * 3)
    layer = Image.new(
        "RGBA",
        (width + layer_margin * 2, height + layer_margin * 2),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(layer)
    box = draw.textbbox(
        (0, 0),
        line.text,
        font=font,
        stroke_width=stroke_width,
    )
    text_width = box[2] - box[0]
    text_height = box[3] - box[1]
    x = layer_margin + (width - text_width) / 2 - box[0]
    y = layer_margin + (height - text_height) / 2 - box[1]
    draw.text(
        (x, y),
        line.text,
        font=font,
        fill=_parse_color(line.fill),
        stroke_width=stroke_width,
        stroke_fill=_parse_color(line.stroke) if line.stroke else None,
    )

    if abs(line.rotation_degrees) > 0.05:
        layer = layer.rotate(
            line.rotation_degrees,
            resample=Image.Resampling.BICUBIC,
            expand=True,
        )

    target_center_x = left + width // 2
    target_center_y = top + height // 2
    paste_x = round(target_center_x - layer.width / 2)
    paste_y = round(target_center_y - layer.height / 2)
    canvas.alpha_composite(layer, (paste_x, paste_y))


def validate_typography_spec(spec: TypographySpec) -> None:
    if not spec.lines:
        raise TypographyRenderUnavailable("typography spec contains no lines")
    if spec.line_order_confidence < 0.75:
        raise TypographyRenderUnavailable("line order confidence is too low")
    if spec.font_match_confidence < 0.70:
        raise TypographyRenderUnavailable("font match confidence is too low")
    for line in spec.lines:
        if line.confidence < 0.75:
            raise TypographyRenderUnavailable(
                f"typography line confidence too low: {line.text}"
            )


def apply_typography(
    canvas: Image.Image,
    spec: TypographySpec,
    settings: Settings,
    *,
    safe_replacements_only: bool = False,
) -> tuple[Image.Image, list[str]]:
    validate_typography_spec(spec)
    result = canvas.convert("RGBA").copy()
    processed: list[str] = []

    for line in spec.lines:
        if safe_replacements_only:
            if line.replacement_mode is not RegionReplacementMode.REPLACE_SOLID:
                continue
            if not line.replacement_fill:
                raise TypographyRenderUnavailable(
                    f"safe replacement fill missing for line: {line.text}"
                )
            left, top, right, bottom = _line_box_pixels(line, result)
            ImageDraw.Draw(result).rectangle(
                (left, top, right, bottom),
                fill=_parse_color(line.replacement_fill),
            )
        elif line.replacement_mode is RegionReplacementMode.REPLACE_SOLID:
            if not line.replacement_fill:
                raise TypographyRenderUnavailable(
                    f"replacement fill missing for line: {line.text}"
                )
            left, top, right, bottom = _line_box_pixels(line, result)
            ImageDraw.Draw(result).rectangle(
                (left, top, right, bottom),
                fill=_parse_color(line.replacement_fill),
            )

        _draw_line(result, line, settings)
        processed.append(line.text)

    if safe_replacements_only and not processed:
        raise TypographyRenderUnavailable(
            "no text lines are explicitly safe for deterministic replacement"
        )
    return result, processed


def render_typography_master(
    spec: TypographySpec,
    settings: Settings,
    output_path: Path,
    *,
    canvas_size: tuple[int, int] = (3000, 3000),
) -> Path:
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    canvas, _ = apply_typography(canvas, spec, settings)

    if canvas.getchannel("A").getbbox() is None:
        raise TypographyRenderUnavailable("typography renderer produced an empty image")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)
    return output_path


def replace_mixed_typography(
    candidate_path: Path,
    spec: TypographySpec,
    settings: Settings,
    output_path: Path,
) -> tuple[Path, list[str]]:
    with Image.open(candidate_path) as source:
        canvas = source.convert("RGBA")

    refined, processed = apply_typography(
        canvas,
        spec,
        settings,
        safe_replacements_only=True,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    refined.save(output_path, format="PNG", optimize=True)
    return output_path, processed

def overlay_typography(
    candidate_path: Path,
    spec: TypographySpec,
    settings: Settings,
    output_path: Path,
) -> tuple[Path, list[str]]:
    with Image.open(candidate_path) as source:
        canvas = source.convert("RGBA")

    refined, processed = apply_typography(canvas, spec, settings)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    refined.save(output_path, format="PNG", optimize=True)
    return output_path, processed
