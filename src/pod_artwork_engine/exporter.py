from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from .contracts import ExportProfile


def _trim_alpha(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A")
    box = alpha.point(lambda value: 255 if value >= 4 else 0).getbbox()
    return image.crop(box) if box else image


def export_master(
    candidate_path: Path,
    output_path: Path,
    profile: ExportProfile | None = None,
) -> Path:
    profile = profile or ExportProfile()
    if profile.format.upper() != "PNG":
        raise ValueError("Phase 1 exporter currently supports PNG only")

    with Image.open(candidate_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
    image = _trim_alpha(image)
    if image.getchannel("A").getbbox() is None:
        raise ValueError("candidate contains no visible artwork")

    max_width = max(1, round(profile.width * profile.max_artwork_width_ratio))
    max_height = max(1, round(profile.height * profile.max_artwork_height_ratio))
    scale = min(max_width / image.width, max_height / image.height)
    rendered_size = (
        max(1, round(image.width * scale)),
        max(1, round(image.height * scale)),
    )
    rendered = image.resize(rendered_size, Image.Resampling.LANCZOS)

    background = (0, 0, 0, 0) if profile.transparent else (255, 255, 255, 255)
    canvas = Image.new("RGBA", (profile.width, profile.height), background)
    x = (profile.width - rendered.width) // 2
    y = (profile.height - rendered.height) // 2
    canvas.alpha_composite(rendered, (x, y))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(
        output_path,
        format="PNG",
        dpi=(profile.dpi, profile.dpi),
        optimize=True,
    )
    return output_path
