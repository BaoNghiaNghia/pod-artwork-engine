"""Pinned optional local Real-ESRGAN Vulkan upscale and VTracer vectorization.

The third-party model is executed from an installed, fixed directory. This
module never downloads, runs a shell, executes a skill or writes durable
preview files. All processing inputs reside in a temporary directory.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

REAL_ESRGAN_MODELS = ("realesrgan-x4plus", "realesrgan-x4plus-anime")
VECTOR_MODES = ("typography_clean", "vector_clean", "sticker_clean")
GPU_TIMEOUT_SECONDS = 150
VECTOR_MAX_EDGE = 1600
OUTPUT_PIXEL_LIMIT = 20_000_000
_GPU_LOCK = threading.Lock()  # One NCNN process per Engine to protect VRAM.


def find_realesrgan_runtime() -> Path | None:
    """Find a verified local tool installation, never invoke PATH executables."""
    override = os.environ.get("POD_REALESRGAN_DIR")
    if override:
        candidates = [Path(override)]
    else:
        candidates = [
            Path(sys.executable).resolve().parent / "runtime" / "realesrgan",
            Path(__file__).resolve().parents[2] / "vendor" / "realesrgan",
        ]
    for root in candidates:
        if (
            (root / "realesrgan-ncnn-vulkan.exe").is_file()
            and all(
                (root / "models" / f"{model}.{ext}").is_file()
                for model in REAL_ESRGAN_MODELS
                for ext in ("bin", "param")
            )
        ):
            return root
    return None


def enhance_capabilities() -> dict[str, Any]:
    try:
        import vtracer  # noqa: F401
        vector_installed = True
    except ImportError:
        vector_installed = False
    root = find_realesrgan_runtime()
    return {
        "vtracer": {"installed": vector_installed, "version": "0.6.15",
                    "vector_modes": list(VECTOR_MODES), "format": "SVG"},
        "realesrgan": {"installed": root is not None,
                       "model_names": list(REAL_ESRGAN_MODELS),
                       "runtime": "ncnn-vulkan", "requires_vulkan_gpu": True},
        "fallback": "pillow",
        "requires_review": True,
    }


def vector_settings(profile: str = "fast", preset: str = "logo") -> dict[str, Any]:
    """Deterministic bounded VTracer settings per family and quality."""
    if profile not in {"fast", "balanced", "max_quality"}:
        raise ValueError("Unsupported vector quality profile")
    if preset not in {"logo", "text", "sticker"}:
        raise ValueError("Unsupported vector preset")
    edge = {"fast": 1600, "balanced": 2300, "max_quality": 3200}[profile]
    precision = {"fast": 3, "balanced": 4, "max_quality": 6}[profile]
    speckle = {"fast": 3, "balanced": 2, "max_quality": 1}[profile]
    return {
        "max_edge": edge,
        "path_precision": precision,
        "filter_speckle": 0 if preset == "text" and profile == "max_quality" else speckle,
        "corner_threshold": 43 if preset == "text" else 55 if preset == "sticker" else 58,
        "length_threshold": 2.0 if profile == "max_quality" else 3.5,
        "max_iterations": 16 if profile == "max_quality" else 10,
    }


def generate_vector_svg(image: Image.Image, *, profile: str = "fast", preset: str = "logo") -> str:
    """Trace a bounded PNG with route-specific curve and small-detail precision."""
    try:
        import vtracer
    except ImportError as exc:
        raise RuntimeError("VTracer not installed") from exc
    if image.width * image.height > 30_000_000:
        raise ValueError("Image exceeds the vectorization source limit")
    settings = vector_settings(profile, preset)
    rgba = image.convert("RGBA")
    if max(rgba.size) > settings["max_edge"]:
        factor = settings["max_edge"] / max(rgba.size)
        size = (max(1, round(rgba.width * factor)),
                max(1, round(rgba.height * factor)))
        rgba = rgba.resize(size, Image.Resampling.LANCZOS)
    with tempfile.TemporaryDirectory(prefix="pod-vector-") as tmp:
        src = Path(tmp) / "artwork.png"
        dest = Path(tmp) / "artwork.svg"
        rgba.save(src, format="PNG")
        vtracer.convert_image_to_svg_py(
            str(src), str(dest),
            colormode="color", hierarchical="stacked", mode="spline",
            filter_speckle=settings["filter_speckle"], color_precision=7, layer_difference=12,
            corner_threshold=settings["corner_threshold"], length_threshold=settings["length_threshold"],
            max_iterations=settings["max_iterations"], splice_threshold=45,
            path_precision=settings["path_precision"],
        )
        if not dest.is_file() or dest.stat().st_size > 8 * 1024 * 1024:
            raise RuntimeError("VTracer returned no bounded SVG")
        svg = dest.read_text(encoding="utf-8")
    if "<svg" not in svg or "<path" not in svg:
        raise RuntimeError("Vectorization produced no SVG path data")
    # Defensive SVG boundary: never serve active or externally linked content.
    sanitized = svg.replace("http://www.w3.org/2000/svg", "")
    if re.search(r"<\s*(script|foreignObject|image|iframe)\b|on[a-z]+\s*=|javascript:|https?://",
                 sanitized, re.IGNORECASE):
        raise RuntimeError("SVG contains unsupported content")
    return svg


def enhance_with_realesrgan(
    image: Image.Image, runtime_root: Path | None = None, *,
    model: str = "realesrgan-x4plus",
    allow_alpha: bool = False,
    wait_for_gpu_seconds: float = 0,
) -> tuple[Image.Image | None, dict[str, str]]:
    """Try genuine Vulkan inference; return (None, reason) for safe fallback."""
    if model not in REAL_ESRGAN_MODELS:
        raise ValueError("Unsupported Real-ESRGAN model")
    root = runtime_root if runtime_root is not None else find_realesrgan_runtime()
    if root is None:
        return None, {"engine": "fallback", "reason": "Real-ESRGAN runtime unavailable"}
    exe = root / "realesrgan-ncnn-vulkan.exe"
    models = root / "models"
    if not exe.is_file() or not all((models / f"{model}.{ext}").is_file()
                                    for ext in ("bin", "param")):
        return None, {"engine": "fallback", "reason": "Real-ESRGAN model unavailable"}
    rgba = image.convert("RGBA")
    # The NCNN runner can introduce halos at alpha edges: preserve
    # alpha-heavy cutouts with the existing gentler raster processing.
    transparent = rgba.getchannel("A").getextrema()[0] < 255
    if transparent and not allow_alpha:
        return None, {"engine": "fallback", "reason": "Transparent image uses alpha-safe raster fallback"}
    if (rgba.width * rgba.height * 4 > OUTPUT_PIXEL_LIMIT
            or max(rgba.size) * 2 > 4500):
        return None, {"engine": "fallback", "reason": "Image too large for safe 2x AI upscale"}
    # Max Quality queues briefly behind a running Vulkan job instead of
    # silently abandoning AI. Fast retains its existing immediate fallback.
    wait = max(0.0, min(float(wait_for_gpu_seconds), 120.0))
    acquired = _GPU_LOCK.acquire(timeout=wait) if wait > 0 else _GPU_LOCK.acquire(blocking=False)
    if not acquired:
        return None, {"engine": "fallback", "reason": "Real-ESRGAN GPU busy; timed out waiting for a safe inference slot"}
    try:
        try:
            with tempfile.TemporaryDirectory(prefix="pod-esrgan-") as tmp:
                input_path = Path(tmp) / "input.png"
                output_path = Path(tmp) / "output.png"
                if transparent:
                    from PIL import Image as PILImage
                    matte = PILImage.new("RGBA", rgba.size, (245, 245, 245, 255))
                    PILImage.alpha_composite(matte, rgba).convert("RGB").save(input_path, "PNG")
                else:
                    rgba.convert("RGB").save(input_path, "PNG")
                args = [
                    str(exe), "-i", str(input_path), "-o", str(output_path),
                    "-m", str(models), "-n", model, "-s", "2",
                    "-t", "128", "-j", "1:1:1", "-f", "png",
                ]
                result = subprocess.run(
                    args, cwd=str(root), shell=False, check=False,
                    capture_output=True, text=True, timeout=GPU_TIMEOUT_SECONDS,
                )
                if result.returncode != 0 or not output_path.is_file():
                    return None, {"engine": "fallback", "reason": "Real-ESRGAN Vulkan inference failed"}
                with Image.open(output_path) as loaded:
                    if loaded.size != (rgba.width * 2, rgba.height * 2):
                        return None, {"engine": "fallback", "reason": "AI upscale returned unexpected dimensions"}
                    output = loaded.convert("RGBA")
                if transparent:
                    # NCNN outputs opaque RGB. Apply the precisely resampled original alpha,
                    # while retaining the original colors near transparent edges, preventing
                    # the model's artificial matte from leaking into POD cutout contours.
                    original_up = rgba.resize(output.size, Image.Resampling.LANCZOS)
                    alpha = original_up.getchannel("A")
                    blend = alpha.point(lambda a: min(255, max(0, round((a - 205) * 255 / 50))))
                    output = Image.composite(output, original_up, blend)
                    output.putalpha(alpha)
                return output, {"engine": "real-esrgan-ncnn-vulkan", "model": model}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None, {"engine": "fallback", "reason": "Real-ESRGAN inference unavailable or timed out"}
    finally:
        _GPU_LOCK.release()
