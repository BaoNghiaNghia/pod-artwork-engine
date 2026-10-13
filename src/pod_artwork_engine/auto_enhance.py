"""Bounded, explainable local artwork routing and conservative raster enhancement.

The classifier is heuristic, not a trained vision model. Every result is a
review-required raster draft; it must not pass the print-ready QC gate.
"""
from __future__ import annotations

import math
from io import BytesIO
from typing import Any, Literal

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from .enhance_engines import enhance_with_realesrgan

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_INPUT_PIXELS = 30_000_000
MAX_OUTPUT_PIXELS = 20_000_000
MAX_OUTPUT_EDGE = 4500

EnhanceMode = Literal[
    "auto", "typography_clean", "vector_clean", "sticker_clean",
    "photo_cutout", "painterly_clean", "metal_preserve", "dark_rescue", "gentle_clean",
]
SUPPORTED_MODES: tuple[str, ...] = (
    "typography_clean", "vector_clean", "sticker_clean", "photo_cutout",
    "painterly_clean", "metal_preserve", "dark_rescue", "gentle_clean",
)
CATEGORY_MODES = {
    "typography": "typography_clean",
    "logo_vector": "vector_clean",
    "sticker_cartoon": "sticker_clean",
    "photo_cutout": "photo_cutout",
    "painterly_textured": "painterly_clean",
    "metal_gloss_effect": "metal_preserve",
    "dark_low_contrast": "dark_rescue",
    "mixed": "gentle_clean",
}


def load_artwork(data: bytes) -> Image.Image:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Image must be between 1 byte and 50 MB")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Only PNG, JPEG or WebP artwork is supported")
            if source.width < 8 or source.height < 8:
                raise ValueError("Image dimensions must be at least 8 x 8")
            if source.width * source.height > MAX_INPUT_PIXELS:
                raise ValueError("Image exceeds the 30 megapixel safety limit")
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.load()
            return image
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid, damaged or oversized image") from exc


def _features(image: Image.Image) -> dict[str, float]:
    thumbnail = image.copy()
    thumbnail.thumbnail((192, 192), Image.Resampling.BILINEAR)
    w, h = thumbnail.size
    pixels = list(thumbnail.getdata())
    visible = [(r, g, b) for r, g, b, a in pixels if a >= 64]
    if not visible:
        raise ValueError("Artwork has no visible pixels")
    count = len(visible)
    opaque_ratio = sum(a >= 250 for _, _, _, a in pixels) / len(pixels)
    visible_ratio = count / len(pixels)
    luminances = [((r * 299 + g * 587 + b * 114) // 1000) for r, g, b in visible]
    saturation = sum((max(p) - min(p)) for p in visible) / count / 255
    bright_ratio = sum(v >= 225 for v in luminances) / count
    dark_ratio = sum(v < 58 for v in luminances) / count
    quantized = {(r // 32, g // 32, b // 32) for r, g, b in visible}
    color_complexity = min(1., len(quantized) / 55)
    # Transparent pixels must never affect foreground contrast or classification.
    mean = sum(luminances) / count
    std = math.sqrt(sum((v - mean) ** 2 for v in luminances) / count)
    # Approximate edge density from adjacent visible pixels at thumbnail scale.
    edges = comparisons = 0
    for y in range(h):
        for x in range(w - 1):
            a = pixels[y * w + x]
            b = pixels[y * w + x + 1]
            if a[3] < 64 or b[3] < 64:
                continue
            comparisons += 1
            edges += max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])) >= 48
    edge_density = edges / max(1, comparisons)
    # Repeating populated horizontal rows are weak evidence of multi-line lettering.
    row_runs = 0
    inside = False
    for y in range(h):
        occupied = sum(pixels[y * w + x][3] >= 64 for x in range(w))
        active = occupied >= max(2, round(w * .16))
        if active and not inside:
            row_runs += 1
        inside = active
    return {
        "visible_ratio": round(visible_ratio, 4),
        "opaque_ratio": round(opaque_ratio, 4),
        "color_complexity": round(color_complexity, 4),
        "saturation": round(saturation, 4),
        "dark_ratio": round(dark_ratio, 4),
        "bright_ratio": round(bright_ratio, 4),
        "contrast": round(min(1., std / 100), 4),
        "edge_density": round(edge_density, 4),
        "row_runs": float(row_runs),
    }


def analyze_artwork(image: Image.Image) -> dict[str, Any]:
    f = _features(image.convert("RGBA"))
    scores: dict[str, float] = {}
    reasons: list[str] = []
    # Dark rescue is a visibility recommendation rather than a semantic claim.
    if f["dark_ratio"] > .78 and f["saturation"] < .28:
        category, confidence = "dark_low_contrast", .83
        reasons = ["Most visible artwork pixels are dark", "Low color separation"]
    elif f["color_complexity"] < .36 and f["row_runs"] >= 2 and f["edge_density"] < .19:
        category, confidence = "typography", .67
        reasons = ["Few flat colors", "Multiple separated horizontal content bands"]
    elif f["color_complexity"] < .36 and f["edge_density"] < .19:
        # Without OCR, stickers cannot be distinguished from logos with certainty.
        category = "sticker_cartoon" if f["saturation"] > .26 and f["row_runs"] <= 2 else "logo_vector"
        confidence = .58
        reasons = ["Limited color palette", "Mostly flat shapes and defined outlines"]
    elif f["saturation"] < .14 and f["bright_ratio"] > .12 and f["color_complexity"] > .42:
        category, confidence = "metal_gloss_effect", .48
        reasons = ["Grayscale highlights and smooth tone variation", "Metal detection is tentative"]
    elif f["color_complexity"] > .7 and f["visible_ratio"] > .80 and f["opaque_ratio"] > .76:
        category, confidence = "photo_cutout", .43
        reasons = ["Dense gradients and color detail", "Photo detection needs visual confirmation"]
    elif f["color_complexity"] > .52:
        category, confidence = "painterly_textured", .46
        reasons = ["Complex color variation", "Preserve texture rather than flattening colors"]
    else:
        category, confidence = "mixed", .35
        reasons = ["Insufficient evidence for a specialized artwork route"]
    # The scores are descriptive features, NOT probabilities or ML confidences.
    scores = {key: value for key, value in f.items() if key != "row_runs"}
    mode = CATEGORY_MODES[category]
    # Uncertain semantic classification always falls back to non-destructive cleanup.
    if confidence < .45 and category not in {"dark_low_contrast"}:
        mode = "gentle_clean"
        reasons.append("Low confidence: conservative processing selected")
    return {
        "category": category,
        "mode": mode,
        "confidence": confidence,
        "reasons": reasons,
        "scores": scores,
        "supported_modes": list(SUPPORTED_MODES),
        "method": "deterministic-local-heuristics-v1",
        "requires_review": True,
        "print_ready": False,
    }


def enhance_artwork(image: Image.Image, *, mode: EnhanceMode = "auto") -> tuple[Image.Image, dict[str, Any]]:
    if mode != "auto" and mode not in SUPPORTED_MODES:
        raise ValueError("Unsupported enhancement mode")
    if image.width * image.height > MAX_INPUT_PIXELS:
        raise ValueError("Image exceeds the 30 megapixel safety limit")
    report = analyze_artwork(image)
    selected = report["mode"] if mode == "auto" else mode
    report = {**report, "mode": selected, "override": mode != "auto"}
    # Real inference only for compatible image types. Flat artwork is
    # vectorized separately through /enhance/vector, not hallucinated here.
    fallback_reason = None
    if selected in {"photo_cutout", "painterly_clean", "metal_preserve"}:
        model = "realesrgan-x4plus"
        improved, meta = enhance_with_realesrgan(image, model=model)
        if improved is not None:
            report.update({
                "engine": "real-esrgan-ncnn-vulkan",
                "model": model,
                "operation": "real-esrgan-vulkan-upscale",
                "output_width": improved.width,
                "output_height": improved.height,
                "warning": "AI reconstructed details can differ from the source. Review before printing.",
            })
            return improved, report
        fallback_reason = meta.get("reason", "Real-ESRGAN not available")
    report["engine"] = "pillow-fallback"
    if fallback_reason:
        report["fallback_reason"] = fallback_reason
    width, height = image.size
    scale = min(2.0, MAX_OUTPUT_EDGE / max(width, height),
                math.sqrt(MAX_OUTPUT_PIXELS / (width * height)))
    new_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    rgba = image.convert("RGBA")
    if new_size != rgba.size:
        rgba = rgba.resize(new_size, Image.Resampling.LANCZOS)
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    if selected in {"typography_clean", "vector_clean", "sticker_clean"}:
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=0.85, percent=105, threshold=2))
    elif selected == "photo_cutout":
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.25, percent=32, threshold=5))
    elif selected == "painterly_clean":
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.15, percent=55, threshold=3))
    elif selected == "metal_preserve":
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.0, percent=48, threshold=4))
    elif selected == "dark_rescue":
        rgb = ImageEnhance.Brightness(rgb).enhance(1.19)
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.0, percent=55, threshold=3))
    else:
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.1, percent=35, threshold=4))
    output = rgb.convert("RGBA")
    output.putalpha(alpha)
    report["output_width"], report["output_height"] = output.size
    report["operation"] = "local-raster-edge-enhancement"
    report["warning"] = (
        "Enhanced raster preview only. Upscaling does not recover missing source "
        "detail or create an SVG. Inspect at print size before approving."
    )
    return output, report
