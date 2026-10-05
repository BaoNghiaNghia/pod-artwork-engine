from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

from .contracts import DesignSpec, ExportProfile, QCGate, QCResult, QualityMode
from .reconstruction import CandidateInfo


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def semantic_qc(
    design_spec: DesignSpec,
    quality_mode: QualityMode,
    *,
    recognized_text: list[str] | None = None,
    used_remote_provider: bool = False,
) -> QCResult:
    reasons: list[str] = []
    score = design_spec.confidence

    expected = [_normalize_text(item) for item in design_spec.exact_text]
    observed = [_normalize_text(item) for item in (recognized_text or [])]
    text_verified = None
    if expected:
        text_verified = bool(observed) and observed == expected
        if not text_verified:
            reasons.append("exact_text_not_verified")
            score = min(score, 0.45)
        else:
            score = min(1.0, score + 0.12)

    if design_spec.artwork_bbox is None:
        reasons.append("artwork_bbox_missing")
        score = min(score, 0.2)
    if design_spec.occlusion >= 0.75:
        reasons.append("heavy_occlusion")
        score = min(score, 0.42)

    threshold = {
        QualityMode.QUICK_2D: 0.20,
        QualityMode.PRINT_READY: 0.35,
        QualityMode.MAX_FIDELITY: 0.50,
    }[quality_mode]

    semantic_provider_required = (
        quality_mode is not QualityMode.QUICK_2D
        and not used_remote_provider
        and "need_semantic_reconstruction" in design_spec.required_capabilities
    )
    if semantic_provider_required:
        reasons.append("semantic_provider_not_used")
        score = min(score, 0.49)

    passed = score >= threshold and "artwork_bbox_missing" not in reasons
    if semantic_provider_required:
        passed = False
    if expected and text_verified is not True and quality_mode is not QualityMode.QUICK_2D:
        passed = False

    return QCResult(
        gate=QCGate.SEMANTIC,
        passed=passed,
        score=max(0.0, min(1.0, score)),
        reasons=reasons,
        metrics={
            "analysis_confidence": round(design_spec.confidence, 4),
            "occlusion": round(design_spec.occlusion, 4),
            "exact_text_required": bool(expected),
            "exact_text_verified": text_verified,
            "remote_provider_used": used_remote_provider,
        },
    )


def _edge_score(image: Image.Image) -> float:
    working = image.convert("L")
    working.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    edges = working.filter(ImageFilter.FIND_EDGES)
    stat = ImageStat.Stat(edges)
    return max(0.0, min(1.0, stat.stddev[0] / 72.0))


def technical_qc(
    output_path: Path,
    candidate: CandidateInfo,
    quality_mode: QualityMode,
    profile: ExportProfile | None = None,
) -> QCResult:
    profile = profile or ExportProfile()
    reasons: list[str] = []

    with Image.open(output_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        size_ok = image.size == (profile.width, profile.height)
        alpha = image.getchannel("A")
        alpha_bbox = alpha.getbbox()
        alpha_range = alpha.getextrema()
        edge_score = _edge_score(image)
        dpi = source.info.get("dpi", (0, 0))

    if not size_ok:
        reasons.append("wrong_output_dimensions")
    if alpha_bbox is None:
        reasons.append("empty_alpha")
    if profile.transparent and alpha_range == (255, 255):
        reasons.append("no_transparent_background")

    native_long_edge = max(candidate.native_width, candidate.native_height)
    required_native = {
        QualityMode.QUICK_2D: 300,
        QualityMode.PRINT_READY: 700,
        QualityMode.MAX_FIDELITY: 1000,
    }[quality_mode]
    resolution_score = min(1.0, native_long_edge / max(1, required_native))
    if native_long_edge < required_native:
        reasons.append("low_effective_source_resolution")

    score_components = [
        1.0 if size_ok else 0.0,
        1.0 if alpha_bbox is not None else 0.0,
        resolution_score,
        edge_score,
    ]
    score = sum(score_components) / len(score_components)

    blocking = {"wrong_output_dimensions", "empty_alpha"}
    passed = not any(reason in blocking for reason in reasons)
    if quality_mode is QualityMode.MAX_FIDELITY and resolution_score < 0.75:
        passed = False
    if quality_mode is QualityMode.PRINT_READY and resolution_score < 0.55:
        passed = False

    return QCResult(
        gate=QCGate.TECHNICAL,
        passed=passed,
        score=max(0.0, min(1.0, score)),
        reasons=reasons,
        metrics={
            "width": profile.width,
            "height": profile.height,
            "native_candidate_width": candidate.native_width,
            "native_candidate_height": candidate.native_height,
            "native_long_edge": native_long_edge,
            "resolution_score": round(resolution_score, 4),
            "edge_score": round(edge_score, 4),
            "alpha_min": alpha_range[0],
            "alpha_max": alpha_range[1],
            "dpi_x": round(float(dpi[0]), 2) if dpi else 0,
            "dpi_y": round(float(dpi[1]), 2) if dpi else 0,
        },
    )
