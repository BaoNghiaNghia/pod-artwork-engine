from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .contracts import RegionReplacementMode, TypographyLine, TypographySpec
from .font_catalog import get_font_catalog, normalize_font_name
from .settings import Settings


class TypographyRenderUnavailable(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_font(
    settings: Settings,
    family: str,
    weight: int = 400,
    *,
    expected_sha256: str = "",
) -> Path | None:
    catalog = get_font_catalog(settings)
    if expected_sha256:
        wanted = catalog.canonicalize(family)
        for entry in catalog.entries:
            if catalog.canonicalize(entry.family) != wanted:
                continue
            try:
                if _sha256_file(entry.path) == expected_sha256:
                    return entry.path
            except OSError:
                continue
        return None

    entry = catalog.resolve(family, weight)
    return entry.path if entry is not None else None


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
    expected_sha256 = (
        line.font_match.font_sha256
        if line.font_match is not None
        and line.font_match.accepted
        and line.font_match.font_sha256
        else ""
    )
    font_path = resolve_font(
        settings,
        line.font_family,
        line.font_weight,
        expected_sha256=expected_sha256,
    )
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
        match = line.font_match
        if (
            match is not None
            and match.accepted
            and match.method == "visual_render_compare_v1"
        ):
            if not match.font_sha256:
                raise TypographyRenderUnavailable(
                    f"visual font match is missing its font fingerprint: {line.text}"
                )
            if normalize_font_name(match.family) != normalize_font_name(
                line.font_family
            ):
                raise TypographyRenderUnavailable(
                    f"visual font evidence family mismatch: {line.text}"
                )
            if match.weight != line.font_weight:
                raise TypographyRenderUnavailable(
                    f"visual font evidence weight mismatch: {line.text}"
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
        replacement_mode = line.replacement_mode
        if safe_replacements_only and replacement_mode not in {
            RegionReplacementMode.REPLACE_SOLID,
            RegionReplacementMode.REPLACE_MASK,
        }:
            continue

        if replacement_mode in {
            RegionReplacementMode.REPLACE_SOLID,
            RegionReplacementMode.REPLACE_MASK,
        }:
            if not line.replacement_fill:
                raise TypographyRenderUnavailable(
                    f"replacement fill missing for line: {line.text}"
                )
            draw = ImageDraw.Draw(result)
            if replacement_mode is RegionReplacementMode.REPLACE_SOLID:
                left, top, right, bottom = _line_box_pixels(line, result)
                draw.rectangle(
                    (left, top, right, bottom),
                    fill=_parse_color(line.replacement_fill),
                )
            else:
                if len(line.replacement_mask) < 3:
                    raise TypographyRenderUnavailable(
                        f"replacement mask requires at least 3 points: {line.text}"
                    )
                pad_x = max(0.01, line.bbox.width * 0.08)
                pad_y = max(0.015, line.bbox.height * 0.20)
                min_x = max(0.0, line.bbox.x - pad_x)
                min_y = max(0.0, line.bbox.y - pad_y)
                max_x = min(1.0, line.bbox.x + line.bbox.width + pad_x)
                max_y = min(1.0, line.bbox.y + line.bbox.height + pad_y)
                if any(
                    point.x < min_x
                    or point.x > max_x
                    or point.y < min_y
                    or point.y > max_y
                    for point in line.replacement_mask
                ):
                    raise TypographyRenderUnavailable(
                        f"replacement mask exceeds guarded text region: {line.text}"
                    )
                mask_points = [
                    (
                        max(0, min(result.width - 1, round(point.x * result.width))),
                        max(0, min(result.height - 1, round(point.y * result.height))),
                    )
                    for point in line.replacement_mask
                ]
                draw.polygon(
                    mask_points,
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
