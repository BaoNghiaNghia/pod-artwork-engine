from __future__ import annotations

import hashlib
import math
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

from .artwork_detection import detect_artwork
from .contracts import PreflightResult


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _edge_sharpness(image: Image.Image) -> float:
    gray = image.convert("L")
    working = gray.copy()
    working.thumbnail((768, 768), Image.Resampling.LANCZOS)
    edges = working.filter(ImageFilter.FIND_EDGES)
    stat = ImageStat.Stat(edges)
    mean = stat.mean[0] / 255.0
    std = stat.stddev[0] / 128.0
    return max(0.0, min(1.0, 0.35 * mean + 0.65 * std))


def _compression_risk(path: Path, image_format: str | None, width: int, height: int) -> float:
    if not image_format or image_format.upper() not in {"JPEG", "JPG"}:
        return 0.0
    bytes_per_pixel = path.stat().st_size / max(1, width * height)
    # Aggressively compressed JPEGs commonly fall below this range. This is a
    # triage signal, not a forensic JPEG-quality estimator.
    if bytes_per_pixel >= 0.8:
        return 0.05
    if bytes_per_pixel >= 0.45:
        return 0.2
    if bytes_per_pixel >= 0.25:
        return 0.45
    if bytes_per_pixel >= 0.12:
        return 0.7
    return 0.9


def _source_quality(width: int, height: int, sharpness: float, compression_risk: float) -> float:
    long_edge = max(width, height)
    resolution_score = max(0.0, min(1.0, math.log2(max(long_edge, 64) / 256) / 3.0))
    score = 0.45 * resolution_score + 0.4 * sharpness + 0.15 * (1.0 - compression_risk)
    return max(0.0, min(1.0, score))


def inspect_image(path: Path) -> PreflightResult:
    detection = detect_artwork(path)
    with Image.open(path) as source:
        source.load()
        orientation = source.getexif().get(274, 1)
        image = ImageOps.exif_transpose(source)
        bands = image.getbands()
        sharpness = _edge_sharpness(image)
        compression = _compression_risk(path, source.format, image.width, image.height)
        quality = _source_quality(image.width, image.height, sharpness, compression)

        warnings: list[str] = []
        if max(image.width, image.height) < 900:
            warnings.append("low_resolution")
        if sharpness < 0.18:
            warnings.append("blur_risk")
        if compression >= 0.65:
            warnings.append("compression_risk")
        if detection.confidence < 0.3:
            warnings.append("artwork_region_uncertain")

        return PreflightResult(
            sha256=sha256_file(path),
            filename=path.name,
            width=image.width,
            height=image.height,
            image_mode=image.mode,
            image_format=source.format,
            file_size_bytes=path.stat().st_size,
            has_alpha="A" in bands or "transparency" in source.info,
            orientation_applied=orientation not in (None, 1),
            blur_score=sharpness,
            compression_risk=compression,
            source_quality=quality,
            artwork_bbox=detection.bbox,
            artwork_confidence=detection.confidence,
            warnings=warnings,
        )
