from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from .contracts import PreflightResult


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_image(path: Path) -> PreflightResult:
    with Image.open(path) as image:
        image.load()
        bands = image.getbands()
        return PreflightResult(
            sha256=sha256_file(path),
            filename=path.name,
            width=image.width,
            height=image.height,
            image_mode=image.mode,
            image_format=image.format,
            file_size_bytes=path.stat().st_size,
            has_alpha="A" in bands or "transparency" in image.info,
        )
