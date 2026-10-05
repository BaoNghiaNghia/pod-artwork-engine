from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

from .contracts import (
    DesignSpec,
    ExportProfile,
    QCGate,
    QCResult,
    QualityMode,
    SemanticJudgeResult,
)
from .qc_policy import DEFAULT_QC_POLICY, QCPolicy
from .reconstruction import CandidateInfo


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def semantic_qc(
    design_spec: DesignSpec,
    quality_mode: QualityMode,
    *,
    recognized_text: list[str] | None = None,
    used_remote_provider: bool = False,
    judge_result: SemanticJudgeResult | None = None,
    policy: QCPolicy | None = None,
) -> QCResult:
    reasons: list[str] = []
    policy = policy or DEFAULT_QC_POLICY
    mode_policy = policy.for_mode(quality_mode)
    score = design_spec.confidence

    expected = [_normalize_text(item) for item in design_spec.exact_text]
    observed = [_normalize_text(item) for item in (recognized_text or [])]
    text_verified: bool | None = None

    judge_values: list[float] = []
    judge_score: float | None = None
    judge_object_fidelity: float | None = None
    if judge_result is not None and judge_result.confidence >= 0.50:
        judge_values = [
            value
            for value in (
                judge_result.exact_text,
                judge_result.layout,
                judge_result.object_fidelity,
                judge_result.color,
                judge_result.texture,
                judge_result.missing_detail,
            )
            if value is not None
        ]
        if judge_values:
            judge_score = sum(judge_values) / len(judge_values)
            score = 0.45 * score + 0.55 * judge_score
        judge_object_fidelity = judge_result.object_fidelity
        for reason in judge_result.reasons:
            normalized = reason.strip()
            if normalized and normalized not in reasons:
                reasons.append(f"judge:{normalized}")

    if expected:
        text_verified = bool(observed) and observed == expected
        if (
            not text_verified
            and judge_result is not None
            and judge_result.exact_text is not None
        ):
            text_verified = judge_result.exact_text >= 0.999

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

    threshold = mode_policy.semantic_min_score

    semantic_provider_required = (
        quality_mode is not QualityMode.QUICK_2D
        and not used_remote_provider
        and "need_semantic_reconstruction" in design_spec.required_capabilities
    )
    if semantic_provider_required:
        reasons.append("semantic_provider_not_used")
        score = min(score, 0.49)

    object_threshold = mode_policy.object_fidelity_min
    object_fidelity_failed = (
        judge_object_fidelity is not None
        and judge_object_fidelity < object_threshold
    )
    if object_fidelity_failed:
        reasons.append("object_fidelity_below_threshold")
        score = min(score, judge_object_fidelity)

    passed = score >= threshold and "artwork_bbox_missing" not in reasons
    if semantic_provider_required or object_fidelity_failed:
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
            "judge_available": judge_result is not None,
            "judge_confidence": (
                round(judge_result.confidence, 4)
                if judge_result is not None
                else None
            ),
            "judge_score": round(judge_score, 4) if judge_score is not None else None,
            "object_fidelity": (
                round(judge_object_fidelity, 4)
                if judge_object_fidelity is not None
                else None
            ),
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "semantic_threshold": round(threshold, 4),
            "object_fidelity_threshold": round(object_threshold, 4),
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
    *,
    policy: QCPolicy | None = None,
) -> QCResult:
    profile = profile or ExportProfile()
    policy = policy or DEFAULT_QC_POLICY
    mode_policy = policy.for_mode(quality_mode)
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
    required_native = mode_policy.required_native_long_edge
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
    if resolution_score < mode_policy.min_resolution_score:
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
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "required_native_long_edge": required_native,
            "min_resolution_score": round(mode_policy.min_resolution_score, 4),
        },
    )
