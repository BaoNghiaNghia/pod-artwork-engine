from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, ImageStat

from .contracts import BoundingBox


@dataclass(frozen=True)
class ArtworkDetection:
    bbox: BoundingBox
    confidence: float
    method: str


def _normalized_bbox(
    box: tuple[int, int, int, int],
    width: int,
    height: int,
) -> BoundingBox:
    left, top, right, bottom = box
    return BoundingBox(
        x=max(0.0, min(1.0, left / width)),
        y=max(0.0, min(1.0, top / height)),
        width=max(1 / width, min(1.0, (right - left) / width)),
        height=max(1 / height, min(1.0, (bottom - top) / height)),
    )


def bbox_to_pixels(bbox: BoundingBox, width: int, height: int) -> tuple[int, int, int, int]:
    left = max(0, min(width - 1, round(bbox.x * width)))
    top = max(0, min(height - 1, round(bbox.y * height)))
    right = max(left + 1, min(width, round((bbox.x + bbox.width) * width)))
    bottom = max(top + 1, min(height, round((bbox.y + bbox.height) * height)))
    return left, top, right, bottom


def _alpha_detection(image: Image.Image) -> ArtworkDetection | None:
    if "A" not in image.getbands():
        return None
    alpha = image.getchannel("A")
    extrema = alpha.getextrema()
    if extrema == (255, 255):
        return None
    mask = alpha.point(lambda value: 255 if value >= 12 else 0)
    box = mask.getbbox()
    if not box:
        return None
    area = (box[2] - box[0]) * (box[3] - box[1])
    ratio = area / max(1, image.width * image.height)
    confidence = max(0.55, min(0.99, 0.72 + min(0.22, ratio * 0.4)))
    return ArtworkDetection(
        bbox=_normalized_bbox(box, image.width, image.height),
        confidence=confidence,
        method="alpha",
    )


def _border_color(image: Image.Image) -> tuple[float, float, float]:
    rgb = image.convert("RGB")
    w, h = rgb.size
    sample = max(1, min(w, h) // 40)
    strips = [
        rgb.crop((0, 0, w, sample)),
        rgb.crop((0, max(0, h - sample), w, h)),
        rgb.crop((0, 0, sample, h)),
        rgb.crop((max(0, w - sample), 0, w, h)),
    ]
    pixels: list[tuple[int, int, int]] = []
    for strip in strips:
        pixels.extend(strip.resize((max(1, strip.width // 4), max(1, strip.height // 4))).getdata())
    if not pixels:
        return 255.0, 255.0, 255.0
    return (
        sum(pixel[0] for pixel in pixels) / len(pixels),
        sum(pixel[1] for pixel in pixels) / len(pixels),
        sum(pixel[2] for pixel in pixels) / len(pixels),
    )


def _foreground_mask(image: Image.Image, threshold: float = 42.0) -> Image.Image:
    rgb = image.convert("RGB")
    background = _border_color(rgb)
    small = rgb.copy()
    max_edge = 768
    small.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

    sx = small.width / rgb.width
    sy = small.height / rgb.height
    pixels = list(small.getdata())
    values = []
    for r, g, b in pixels:
        distance = math.sqrt(
            (r - background[0]) ** 2
            + (g - background[1]) ** 2
            + (b - background[2]) ** 2
        )
        values.append(255 if distance >= threshold else 0)

    mask = Image.new("L", small.size)
    mask.putdata(values)
    box = mask.getbbox()
    if box is None:
        return Image.new("L", rgb.size, 0)

    expanded = (
        max(0, int(box[0] / max(sx, 1e-6))),
        max(0, int(box[1] / max(sy, 1e-6))),
        min(rgb.width, int(math.ceil(box[2] / max(sx, 1e-6)))),
        min(rgb.height, int(math.ceil(box[3] / max(sy, 1e-6)))),
    )
    full = Image.new("L", rgb.size, 0)
    full.paste(255, expanded)
    return full


def detect_artwork(path: Path) -> ArtworkDetection:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")

    alpha = _alpha_detection(image)
    if alpha is not None:
        return alpha

    mask = _foreground_mask(image)
    box = mask.getbbox()
    if box:
        area = (box[2] - box[0]) * (box[3] - box[1])
        area_ratio = area / max(1, image.width * image.height)
        touches_edge = (
            box[0] <= image.width * 0.02
            or box[1] <= image.height * 0.02
            or box[2] >= image.width * 0.98
            or box[3] >= image.height * 0.98
        )
        if 0.01 <= area_ratio <= 0.85 and not touches_edge:
            confidence = max(0.35, min(0.82, 0.62 - abs(area_ratio - 0.28) * 0.45))
            return ArtworkDetection(
                bbox=_normalized_bbox(box, image.width, image.height),
                confidence=confidence,
                method="border_contrast",
            )

    # Conservative fallback: most apparel artwork is centered in the torso/print
    # region. This is intentionally low-confidence so the router can escalate.
    left = round(image.width * 0.15)
    top = round(image.height * 0.12)
    right = round(image.width * 0.85)
    bottom = round(image.height * 0.88)
    return ArtworkDetection(
        bbox=_normalized_bbox((left, top, right, bottom), image.width, image.height),
        confidence=0.22,
        method="center_fallback",
    )


def crop_artwork(path: Path, bbox: BoundingBox) -> Image.Image:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
    return image.crop(bbox_to_pixels(bbox, image.width, image.height))
