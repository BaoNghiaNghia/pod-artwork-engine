from __future__ import annotations

from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from .checkpoints import CheckpointManager


PreviewKind = Literal["local_draft", "ai_candidate"]


def preview_kind_for_job(jobs_dir: Path, job_id: str) -> PreviewKind | None:
    checkpoint = CheckpointManager(jobs_dir).payload(job_id, "candidate")
    if not isinstance(checkpoint, dict):
        return None
    path = Path(str(checkpoint.get("path", ""))).resolve()
    root = (jobs_dir / job_id / "temp").resolve()
    if not path.is_file() or not path.is_relative_to(root):
        return None
    if checkpoint.get("used_remote") and not checkpoint.get("local_baseline"):
        return "ai_candidate"
    if checkpoint.get("local_baseline"):
        return "local_draft"
    return None


def marked_preview_for_job(
    jobs_dir: Path, job_id: str, *, kind: PreviewKind, review_required: bool
) -> Path | None:
    """Return an on-demand, visibly marked image, never a print export."""
    if not review_required or preview_kind_for_job(jobs_dir, job_id) != kind:
        return None

    checkpoint = CheckpointManager(jobs_dir).payload(job_id, "candidate")
    assert isinstance(checkpoint, dict)
    candidate = Path(str(checkpoint["path"])).resolve()
    root = (jobs_dir / job_id).resolve()
    path = root / "preview" / ("local-crop.png" if kind == "local_draft" else "ai-candidate.png")
    if path.is_file() and path.stat().st_mtime_ns >= candidate.stat().st_mtime_ns:
        return path

    with Image.open(candidate) as source:
        image = source.convert("RGBA")
        image.thumbnail((900, 900), Image.Resampling.LANCZOS)
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        height = max(34, round(image.height * 0.08))
        draw.rectangle(
            (0, image.height - height, image.width, image.height),
            fill=(125, 40, 25, 225),
        )
        message = (
            "LOCAL DRAFT - NOT PRINT READY" if kind == "local_draft"
            else "AI CANDIDATE - REVIEW REQUIRED"
        )
        draw.text(
            (12, image.height - height + 10),
            message,
            fill=(255, 255, 255, 255),
            font=ImageFont.load_default(),
        )
        image = Image.alpha_composite(image, overlay)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG")
    return path


def local_draft_for_job(jobs_dir: Path, job_id: str, *, blocked: bool) -> Path | None:
    return marked_preview_for_job(jobs_dir, job_id, kind="local_draft", review_required=blocked)
