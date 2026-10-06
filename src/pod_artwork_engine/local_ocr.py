from __future__ import annotations

import csv
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .artwork_detection import crop_artwork
from .contracts import BoundingBox, TypographyLine, TypographySpec
from .settings import Settings


class LocalOCRUnavailable(RuntimeError):
    pass


BUNDLED_TESSERACT_RELATIVE = Path("runtime") / "tesseract" / "tesseract.exe"


@dataclass(frozen=True)
class LocalOCRBackend:
    executable: Path
    source: str


@dataclass(frozen=True)
class LocalOCRResult:
    exact_text: list[str]
    typography: TypographySpec
    backend: str
    backend_version: str = ""
    backend_source: str = ""
    executable_sha256: str = ""


def _runtime_roots() -> list[Path]:
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    else:
        roots.append(Path(__file__).resolve().parents[2])

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _bundled_language_data_available(executable: Path, language: str) -> bool:
    tessdata = executable.parent / "tessdata"
    languages = [item.strip() for item in language.split("+") if item.strip()]
    if not tessdata.is_dir() or not languages:
        return False
    return all((tessdata / f"{item}.traineddata").is_file() for item in languages)


def _resolve_tesseract(settings: Settings) -> LocalOCRBackend | None:
    if settings.tesseract_path is not None:
        candidate = settings.tesseract_path.expanduser().resolve()
        if candidate.is_file():
            return LocalOCRBackend(candidate, "configured")
        return None

    for root in _runtime_roots():
        candidate = root / BUNDLED_TESSERACT_RELATIVE
        if candidate.is_file() and _bundled_language_data_available(
            candidate,
            settings.tesseract_language,
        ):
            return LocalOCRBackend(candidate.resolve(), "bundled")

    located = shutil.which("tesseract")
    if located:
        return LocalOCRBackend(Path(located).resolve(), "path")

    candidates = [
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    installed = next((path for path in candidates if path.is_file()), None)
    if installed is not None:
        return LocalOCRBackend(installed.resolve(), "system_install")
    return None


def _find_tesseract(settings: Settings) -> Path | None:
    backend = _resolve_tesseract(settings)
    return backend.executable if backend is not None else None


def available(settings: Settings) -> bool:
    return settings.local_ocr_enabled and _resolve_tesseract(settings) is not None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise LocalOCRUnavailable(
            f"local OCR backend fingerprint failed: {type(exc).__name__}"
        ) from exc
    return digest.hexdigest()


def _version(executable: Path) -> str:
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    first = (result.stdout or result.stderr).splitlines()
    return first[0].strip()[:120] if first else ""


def backend_status(settings: Settings) -> dict[str, object]:
    if not settings.local_ocr_enabled:
        return {
            "available": False,
            "backend": "tesseract",
            "source": "disabled",
            "version": "",
        }

    backend = _resolve_tesseract(settings)
    if backend is None:
        return {
            "available": False,
            "backend": "tesseract",
            "source": "unavailable",
            "version": "",
        }
    return {
        "available": True,
        "backend": "tesseract",
        "source": backend.source,
        "version": _version(backend.executable),
    }


def _run_tsv(executable: Path, image_path: Path, language: str) -> str:
    env = os.environ.copy()
    tessdata = executable.parent / "tessdata"
    if tessdata.is_dir():
        env["TESSDATA_PREFIX"] = str(tessdata)

    try:
        result = subprocess.run(
            [
                str(executable),
                str(image_path),
                "stdout",
                "-l",
                language,
                "--psm",
                "11",
                "tsv",
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalOCRUnavailable(f"local OCR failed to start: {exc}") from exc

    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise LocalOCRUnavailable(f"local OCR returned {result.returncode}: {message[:240]}")
    return result.stdout


def _parse_tsv(tsv: str, width: int, height: int) -> LocalOCRResult:
    groups: dict[tuple[int, int, int], list[dict[str, object]]] = {}
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            confidence = float(row.get("conf") or -1)
            if confidence < 70:
                continue
            left = int(row.get("left") or 0)
            top = int(row.get("top") or 0)
            word_width = int(row.get("width") or 0)
            word_height = int(row.get("height") or 0)
            key = (
                int(row.get("block_num") or 0),
                int(row.get("par_num") or 0),
                int(row.get("line_num") or 0),
            )
        except (TypeError, ValueError):
            continue
        if word_width <= 0 or word_height <= 0:
            continue
        groups.setdefault(key, []).append(
            {
                "text": text,
                "confidence": confidence,
                "left": left,
                "top": top,
                "right": left + word_width,
                "bottom": top + word_height,
            }
        )

    lines: list[tuple[int, int, TypographyLine]] = []
    for words in groups.values():
        words.sort(key=lambda item: int(item["left"]))
        text = " ".join(str(item["text"]) for item in words).strip()
        if not text:
            continue
        left = min(int(item["left"]) for item in words)
        top = min(int(item["top"]) for item in words)
        right = max(int(item["right"]) for item in words)
        bottom = max(int(item["bottom"]) for item in words)
        confidence = sum(float(item["confidence"]) for item in words) / (100 * len(words))
        lines.append(
            (
                top,
                left,
                TypographyLine(
                    text=text,
                    bbox=BoundingBox(
                        x=max(0.0, min(1.0, left / max(1, width))),
                        y=max(0.0, min(1.0, top / max(1, height))),
                        width=max(1 / max(1, width), min(1.0, (right - left) / max(1, width))),
                        height=max(1 / max(1, height), min(1.0, (bottom - top) / max(1, height))),
                    ),
                    confidence=max(0.0, min(1.0, confidence)),
                ),
            )
        )

    lines.sort(key=lambda item: (item[0], item[1]))
    typography_lines = [item[2] for item in lines]
    mean_confidence = (
        sum(line.confidence for line in typography_lines) / len(typography_lines)
        if typography_lines
        else 0.0
    )
    return LocalOCRResult(
        exact_text=[line.text for line in typography_lines],
        typography=TypographySpec(
            lines=typography_lines,
            line_order_confidence=mean_confidence,
            font_match_confidence=0.0,
            evidence_provider="tesseract",
            evidence_version="",
        ),
        backend="tesseract",
    )


def analyze_artwork_text(
    source_path: Path,
    artwork_bbox: BoundingBox,
    settings: Settings,
) -> LocalOCRResult:
    if not settings.local_ocr_enabled:
        raise LocalOCRUnavailable("local OCR is disabled")
    backend = _resolve_tesseract(settings)
    if backend is None:
        raise LocalOCRUnavailable("Tesseract OCR is not installed or configured")
    executable = backend.executable

    crop = crop_artwork(source_path, artwork_bbox).convert("RGB")
    # Avoid sending huge source photographs through OCR. Text geometry remains
    # normalized because coordinates are relative to the temporary crop.
    crop.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
    with tempfile.TemporaryDirectory(prefix="pod-ocr-") as temp_dir:
        image_path = Path(temp_dir) / "artwork.png"
        crop.save(image_path, format="PNG", optimize=True)
        parsed = _parse_tsv(
            _run_tsv(executable, image_path, settings.tesseract_language),
            crop.width,
            crop.height,
        )
    backend_version = _version(executable)
    return LocalOCRResult(
        exact_text=parsed.exact_text,
        typography=parsed.typography.model_copy(
            update={"evidence_version": backend_version}
        ),
        backend=parsed.backend,
        backend_version=backend_version,
        backend_source=backend.source,
        executable_sha256=_sha256_file(executable),
    )
