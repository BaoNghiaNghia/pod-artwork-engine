"""Bounded HQ candidate rendering for POD art. Independent of print-ready QC.

Pixel scores estimate structural differences, not perceived quality or semantic
accuracy. The original file is never modified; the UI retains user choice.
"""
from __future__ import annotations

import math
import time
from typing import Any, Literal

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, ImageStat

from .auto_enhance import (
    MAX_INPUT_PIXELS, MAX_OUTPUT_EDGE, SUPPORTED_MODES, analyze_artwork, enhance_artwork,
)
from .enhance_engines import OUTPUT_PIXEL_LIMIT, enhance_with_realesrgan, available_realesrgan_model_names

QualityProfile = Literal["fast", "balanced", "max_quality"]
CandidateVariant = Literal["standard", "detail", "texture"]
DetailStrength = Literal["natural", "clear", "strong"]
PROFILE_VARIANTS: dict[str, tuple[str, ...]] = {
    "fast": ("standard",),
    "balanced": ("standard", "detail"),
    "max_quality": ("standard", "detail", "texture"),
}
MAX_HQ_OUTPUT_PIXELS = 30_000_000
SINGLE_PASS_MAX_OUTPUT_PIXELS = OUTPUT_PIXEL_LIMIT
SINGLE_PASS_MAX_EDGE = 4500
MAX_TILED_GPU_PASSES = 16


def refine_large_with_realesrgan(
    image: Image.Image, *, model: str, tile_size: int = 1536, halo: int = 48,
    time_budget_seconds: float = 420,
) -> tuple[Image.Image | None, dict[str, Any]]:
    """Refine a print-size raster via bounded 2x inference and 1x reconstruction.

    Each source-resolution tile includes an overlap halo to limit visible seams.
    This is AI detail refinement at the *original* size, not an upscale claim.
    All-or-nothing: never serve a partly processed canvas if one tile fails.
    """
    if tile_size < 32 or halo < 0 or halo >= tile_size // 4:
        raise ValueError("Unsupported tiled inference geometry")
    columns = math.ceil(image.width / tile_size)
    rows = math.ceil(image.height / tile_size)
    total = columns * rows
    if total > MAX_TILED_GPU_PASSES:
        return None, {"reason": f"Artwork requires {total} GPU tiles; safety limit is {MAX_TILED_GPU_PASSES}"}
    original = image.convert("RGBA")
    canvas = original.copy()
    start = time.monotonic()
    processed = 0
    for y in range(0, original.height, tile_size):
        for x in range(0, original.width, tile_size):
            if time.monotonic() - start > time_budget_seconds:
                return None, {"reason": "Large artwork GPU tile processing exceeded time budget"}
            x1 = min(x + tile_size, original.width)
            y1 = min(y + tile_size, original.height)
            core = original.crop((x, y, x1, y1))
            if core.getchannel("A").getbbox() is None:
                # Empty transparent areas require no inference.
                continue
            left = max(0, x - halo)
            top = max(0, y - halo)
            right = min(original.width, x1 + halo)
            bottom = min(original.height, y1 + halo)
            tile = original.crop((left, top, right, bottom))
            inferred, info = enhance_with_realesrgan(
                tile, model=model, allow_alpha=True, wait_for_gpu_seconds=90,
            )
            if inferred is None:
                return None, {"reason": f"GPU tile {processed + 1}/{total}: {info.get('reason', 'inference failed')}"}
            if inferred.size != (tile.width * 2, tile.height * 2):
                return None, {"reason": "GPU tile returned invalid output dimensions"}
            restored = inferred.resize(tile.size, Image.Resampling.LANCZOS)
            center = restored.crop((x - left, y - top, x1 - left, y1 - top))
            # Lock the source mask exactly. AI may refine foreground RGB, never
            # enlarge/cut out the artwork or erase a thin transparent border.
            center.putalpha(core.getchannel("A"))
            canvas.paste(center, (x, y))
            processed += 1
    return canvas, {"tile_count": processed, "tiling": "overlap-halo-2x-to-source"}


def upscale_alpha_safe(source: Image.Image, target: tuple[int, int], *, sharpen: float = 1.0) -> Image.Image:
    """Preserve straight-alpha color edges; sharpen the *mask*, not hidden RGB.
    
    Pillow RGBA LANCZOS performs premultiplied resampling. UnsharpMask on
    non-premultiplied RGB can create dark or light fringes at transparent edges.
    """
    rgba = source.convert("RGBA")
    output = rgba.resize(target, Image.Resampling.LANCZOS) if rgba.size != target else rgba.copy()
    alpha = output.getchannel("A")
    if sharpen > 1 and alpha.getextrema()[0] < 255:
        # Mask outline enhancement only; do not invent colors in transparent pixels.
        percent = min(115, int((sharpen - 1) * 115))
        alpha = alpha.filter(ImageFilter.UnsharpMask(radius=.85, percent=percent, threshold=3))
        output.putalpha(alpha)
    elif sharpen > 1:
        output = output.filter(ImageFilter.UnsharpMask(radius=0.9, percent=min(160, int(sharpen * 62)), threshold=3))
    return output


def _safe_target(image: Image.Image, variant: str) -> tuple[int, int]:
    # Preserve a 4500x5400 print canvas without trying to upscale it to >30MP.
    w, h = image.size
    limit = min(MAX_HQ_OUTPUT_PIXELS / (w * h), (MAX_OUTPUT_EDGE * 5400 / 4500 / max(w, h)) ** 2)
    scale = min(2 if variant in {"detail", "texture"} else 1.5, math.sqrt(limit))
    # Preserve original dimensions for already-large sources.
    if scale < 1:
        scale = 1 if w * h <= MAX_HQ_OUTPUT_PIXELS else math.sqrt(MAX_HQ_OUTPUT_PIXELS / (w * h))
    return max(1, round(w * scale)), max(1, round(h * scale))


def technical_score(source: Image.Image, candidate: Image.Image) -> dict[str, float]:
    """Bounded heuristic: large alpha/color drift is penalized, sharper outlines rewarded.

    The 'score' is NOT a blind image quality model and cannot validate typography.
    """
    w = 192
    a = source.convert("RGBA").resize((w, w), Image.Resampling.BILINEAR)
    b = candidate.convert("RGBA").resize((w, w), Image.Resampling.BILINEAR)
    alpha_drift = ImageStat.Stat(ImageChops.difference(a.getchannel("A"), b.getchannel("A"))).mean[0] / 255
    # Composite both over the same background; ignore hidden transparent RGB.
    bg = Image.new("RGBA", a.size, (255, 255, 255, 255))
    a_gray = ImageOps.grayscale(Image.alpha_composite(bg, a).convert("RGB"))
    b_gray = ImageOps.grayscale(Image.alpha_composite(bg, b).convert("RGB"))
    edges_a = a_gray.filter(ImageFilter.FIND_EDGES)
    edges_b = b_gray.filter(ImageFilter.FIND_EDGES)
    strength_a = ImageStat.Stat(edges_a).stddev[0]
    strength_b = ImageStat.Stat(edges_b).stddev[0]
    edge_gain = max(-1., min(1., (strength_b - strength_a) / max(1., strength_a)))
    color_drift = ImageStat.Stat(ImageChops.difference(a_gray, b_gray)).mean[0] / 255
    # Large modifications or erased details must not receive a "better" score
    # merely because they create more harsh edges.
    score = max(0., min(100., 70. + 12. * max(-1, min(.6, edge_gain)) -
                             72. * alpha_drift - 54. * color_drift))
    return {
        "technical_score": round(score, 2),
        "edge_gain": round(edge_gain, 4),
        "alpha_drift": round(alpha_drift, 4),
        "color_drift": round(color_drift, 4),
    }


def apply_detail_strength(image: Image.Image, strength: DetailStrength) -> Image.Image:
    """Make genuine model refinement visible inside opaque artwork, not on alpha edges.

    This does not recreate missing pixels. The alpha mask and thin semitransparent
    contours are unchanged to avoid white/dark halos on transparent POD output.
    """
    if strength not in {"natural", "clear", "strong"}:
        raise ValueError("Unsupported detail strength")
    if strength == "natural":
        return image
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    sharpened = rgb.filter(ImageFilter.UnsharpMask(
        radius=.85 if strength == "clear" else 1.05,
        percent=95 if strength == "clear" else 180,
        threshold=2,
    ))
    interior = alpha.point(lambda value: 255 if value >= 245 else 0)
    result = Image.composite(sharpened, rgb, interior).convert("RGBA")
    result.putalpha(alpha)
    return result


def quality_candidate(
    image: Image.Image, *, mode: str = "auto",
    profile: QualityProfile = "fast", variant: CandidateVariant = "standard",
    model: str = "auto", detail_strength: DetailStrength = "natural",
) -> tuple[Image.Image, dict[str, Any]]:
    if profile not in PROFILE_VARIANTS or variant not in PROFILE_VARIANTS.get(profile, ()):
        raise ValueError("Unsupported quality profile / candidate variant")
    if mode != "auto" and mode not in SUPPORTED_MODES:
        raise ValueError("Unsupported enhancement mode")
    if image.width * image.height > MAX_INPUT_PIXELS:
        raise ValueError("Image exceeds the 30 megapixel safety limit")
    if detail_strength not in {"natural", "clear", "strong"}:
        raise ValueError("Unsupported detail strength")
    if model != "auto":
        if model not in available_realesrgan_model_names():
            raise ValueError(f"Requested AI model '{model}' is not installed")
        if variant != "detail" or profile == "fast":
            raise ValueError("Manual AI selection requires Balanced or Max Quality detail candidate")
    if profile == "fast":
        output, report = enhance_artwork(image, mode=mode)
    else:
        route = analyze_artwork(image)
        selected = route["mode"] if mode == "auto" else mode
        report = {**route, "mode": selected, "override": mode != "auto"}
        target = _safe_target(image, variant)
        # Two model families, only when the source is opaque and 2x fits bounded
        # output. Transparent/large artwork is carefully handled locally instead.
        model_name = model if model != "auto" else (
            "realesrgan-x4plus-anime" if selected == "sticker_clean" else "realesrgan-x4plus"
        )
        use_model = (variant == "detail" and
                     (model != "auto" or selected in {
                         "sticker_clean", "photo_cutout", "painterly_clean", "metal_preserve"}))
        ai_output = None
        tiled = False
        if use_model:
            oversized = (image.width * image.height * 4 > SINGLE_PASS_MAX_OUTPUT_PIXELS
                         or max(image.size) * 2 > SINGLE_PASS_MAX_EDGE)
            if oversized and profile == "max_quality":
                # AI inference on bounded overlapping tiles; keep the original
                # print canvas size instead of allocating a 100 MP output.
                ai_output, meta = refine_large_with_realesrgan(image, model=model_name)
                tiled = ai_output is not None
            else:
                ai_output, meta = enhance_with_realesrgan(
                    image, model=model_name, allow_alpha=profile == "max_quality" or model != "auto",
                    wait_for_gpu_seconds=90 if profile == "max_quality" else 0,
                )
            if ai_output is None:
                report["fallback_reason"] = meta.get("reason", "Vulkan inference unavailable")
                if model != "auto":
                    raise ValueError("Selected AI model failed: " + report["fallback_reason"])
        if ai_output is not None:
            output = apply_detail_strength(ai_output, detail_strength)
            report.update(engine="real-esrgan-ncnn-vulkan", model=model_name,
                          operation="hq-tiled-ai-detail-refine" if tiled
                          else "hq-model-super-resolution")
            if tiled:
                report["tile_count"] = meta["tile_count"]
        else:
            # Variant methods intentionally differ: no texture flattening on
            # photo/painterly; line art uses alpha contour + precise resizing.
            output = upscale_alpha_safe(image, target, sharpen=
                                       1.55 if variant == "detail" else 1.18)
            if variant == "texture" and selected not in {"vector_clean", "typography_clean", "sticker_clean"}:
                if output.getchannel("A").getextrema()[0] == 255:
                    output = ImageEnhance.Contrast(output).enhance(1.04)
                    output = output.filter(ImageFilter.UnsharpMask(radius=1.4, percent=65, threshold=5))
            report.update(engine="pillow-hq", operation=f"hq-{variant}-alpha-safe")
        report.update(detail_strength=detail_strength if ai_output is not None else "natural",
                      output_width=output.width, output_height=output.height,
                      warning="Draft candidate: inspect color, outlines and lettering before printing.")
    report.update(profile=profile, variant=variant, print_ready=False, requires_review=True)
    report.update(technical_score(image, output))
    return output, report
