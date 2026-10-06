from __future__ import annotations

from .contracts import (
    DesignSpec,
    ExportProfile,
    MaterialSeparationDisposition,
    MaterialSeparationEvidence,
    PreflightResult,
    RegionConfidenceMapEvidence,
    SuperResolutionDisposition,
    SuperResolutionReadinessEvidence,
    TextureHandlingDisposition,
    TextureHandlingEvidence,
)


NATIVE_SCALE_TOLERANCE = 1.25
LOCAL_SR_SCALE_MAX = 2.0
REMOTE_SR_SCALE_MAX = 4.0
LOCAL_SR_SOURCE_QUALITY_MIN = 0.75
LOCAL_SR_COMPRESSION_MAX = 0.25
LOCAL_SR_REGION_CONFIDENCE_MIN = 0.65
REMOTE_SR_SOURCE_QUALITY_MIN = 0.60
REMOTE_SR_COMPRESSION_MAX = 0.40
REMOTE_SR_REGION_CONFIDENCE_MIN = 0.50
MIN_EVIDENCE_REGION_CONFIDENCE = 0.35


def _native_artwork_size(
    preflight: PreflightResult,
    design_spec: DesignSpec,
) -> tuple[int, int]:
    bbox = design_spec.artwork_bbox or preflight.artwork_bbox
    if bbox is None:
        return 0, 0
    return (
        max(1, round(preflight.width * bbox.width)),
        max(1, round(preflight.height * bbox.height)),
    )


def _target_artwork_size(
    native_width: int,
    native_height: int,
    profile: ExportProfile,
) -> tuple[int, int, float]:
    if native_width <= 0 or native_height <= 0:
        return 0, 0, 0.0

    max_width = max(1, round(profile.width * profile.max_artwork_width_ratio))
    max_height = max(1, round(profile.height * profile.max_artwork_height_ratio))
    scale = min(max_width / native_width, max_height / native_height)
    return (
        max(1, round(native_width * scale)),
        max(1, round(native_height * scale)),
        max(0.0, scale),
    )


def build_super_resolution_readiness(
    preflight: PreflightResult,
    design_spec: DesignSpec,
    material: MaterialSeparationEvidence,
    texture: TextureHandlingEvidence,
    region_map: RegionConfidenceMapEvidence,
    *,
    profile: ExportProfile,
    required_native_long_edge: int,
    primary_index: int,
    provider_available: bool,
) -> SuperResolutionReadinessEvidence:
    bbox = design_spec.artwork_bbox or preflight.artwork_bbox
    common = {
        "primary_index": primary_index,
        "provider_available": provider_available,
        "artwork_bbox": bbox,
        "required_native_long_edge": required_native_long_edge,
        "source_quality": preflight.source_quality,
        "compression_risk": preflight.compression_risk,
        "region_mean_confidence": region_map.mean_confidence,
        "texture_disposition": texture.disposition,
        "material_disposition": material.disposition,
    }

    if bbox is None:
        return SuperResolutionReadinessEvidence(
            disposition=SuperResolutionDisposition.MANUAL_REVIEW,
            confidence=0.0,
            fail_closed=True,
            reason_codes=["artwork_bbox_unavailable"],
            missing_capabilities=["need_artwork_localization", "need_manual_review"],
            **common,
        )

    semantic_or_manual_reasons: list[str] = []
    missing_capabilities: list[str] = []

    if material.disposition is MaterialSeparationDisposition.MANUAL_REVIEW:
        semantic_or_manual_reasons.append("material_separation_unresolved")
        missing_capabilities.extend(
            ["need_material_separation_verification", "need_manual_review"]
        )
    elif material.disposition is MaterialSeparationDisposition.SEMANTIC_REQUIRED:
        semantic_or_manual_reasons.append("material_separation_requires_semantic_handling")
        missing_capabilities.extend(
            ["need_semantic_reconstruction", "need_material_separation"]
        )

    if texture.disposition is TextureHandlingDisposition.MANUAL_REVIEW:
        semantic_or_manual_reasons.append("texture_handling_unresolved")
        missing_capabilities.extend(["need_texture_verification", "need_manual_review"])
    elif texture.disposition is TextureHandlingDisposition.SEMANTIC_REQUIRED:
        semantic_or_manual_reasons.append("texture_requires_semantic_handling")
        missing_capabilities.extend(
            ["need_semantic_reconstruction", "need_texture_reconstruction"]
        )

    if semantic_or_manual_reasons:
        return SuperResolutionReadinessEvidence(
            disposition=SuperResolutionDisposition.MANUAL_REVIEW,
            confidence=max(
                0.0,
                min(0.49, max(material.confidence, texture.confidence)),
            ),
            fail_closed=True,
            reason_codes=sorted(set(semantic_or_manual_reasons)),
            missing_capabilities=sorted(set(missing_capabilities)),
            **common,
        )

    if (
        "region_evidence_unavailable" in region_map.reason_codes
        or not region_map.cells
        or region_map.mean_confidence < MIN_EVIDENCE_REGION_CONFIDENCE
    ):
        return SuperResolutionReadinessEvidence(
            disposition=SuperResolutionDisposition.MANUAL_REVIEW,
            confidence=max(0.0, min(0.49, region_map.mean_confidence)),
            fail_closed=True,
            reason_codes=["regional_sr_evidence_insufficient"],
            missing_capabilities=["need_region_evidence", "need_manual_review"],
            **common,
        )

    native_width, native_height = _native_artwork_size(preflight, design_spec)
    target_width, target_height, scale_factor = _target_artwork_size(
        native_width,
        native_height,
        profile,
    )
    native_long_edge = max(native_width, native_height)
    target_long_edge = max(target_width, target_height)
    measured = {
        **common,
        "native_artwork_width": native_width,
        "native_artwork_height": native_height,
        "native_long_edge": native_long_edge,
        "target_artwork_width": target_width,
        "target_artwork_height": target_height,
        "target_long_edge": target_long_edge,
        "estimated_scale_factor": scale_factor,
    }

    if native_width <= 0 or native_height <= 0 or scale_factor <= 0:
        return SuperResolutionReadinessEvidence(
            disposition=SuperResolutionDisposition.MANUAL_REVIEW,
            confidence=0.0,
            fail_closed=True,
            reason_codes=["sr_size_evidence_unavailable"],
            missing_capabilities=["need_manual_review"],
            **measured,
        )

    if (
        scale_factor <= NATIVE_SCALE_TOLERANCE
        and native_long_edge >= required_native_long_edge
    ):
        confidence = (
            0.35 * preflight.source_quality
            + 0.25 * (1.0 - preflight.compression_risk)
            + 0.20 * region_map.mean_confidence
            + 0.20 * min(1.0, NATIVE_SCALE_TOLERANCE / max(1.0, scale_factor))
        )
        return SuperResolutionReadinessEvidence(
            disposition=SuperResolutionDisposition.NATIVE_SUFFICIENT,
            confidence=max(0.0, min(1.0, confidence)),
            fail_closed=False,
            reason_codes=["native_resolution_sufficient_for_print_target"],
            **measured,
        )

    if scale_factor > REMOTE_SR_SCALE_MAX:
        return SuperResolutionReadinessEvidence(
            disposition=SuperResolutionDisposition.MANUAL_REVIEW,
            confidence=max(
                0.0,
                min(
                    0.49,
                    0.25 * preflight.source_quality
                    + 0.20 * (1.0 - preflight.compression_risk),
                ),
            ),
            fail_closed=True,
            reason_codes=["required_scale_factor_exceeds_safe_sr_readiness"],
            missing_capabilities=[
                "need_higher_resolution_reference",
                "need_manual_review",
            ],
            **measured,
        )

    if (
        preflight.source_quality < 0.45
        or preflight.compression_risk >= 0.55
    ):
        reasons: list[str] = []
        if preflight.source_quality < 0.45:
            reasons.append("source_quality_too_low_for_sr")
        if preflight.compression_risk >= 0.55:
            reasons.append("compression_too_high_for_sr")
        return SuperResolutionReadinessEvidence(
            disposition=SuperResolutionDisposition.MANUAL_REVIEW,
            confidence=max(
                0.0,
                min(
                    0.49,
                    0.30 * preflight.source_quality
                    + 0.20 * (1.0 - preflight.compression_risk),
                ),
            ),
            fail_closed=True,
            reason_codes=reasons,
            missing_capabilities=[
                "need_higher_resolution_reference",
                "need_manual_review",
            ],
            **measured,
        )

    local_candidate = (
        scale_factor <= LOCAL_SR_SCALE_MAX
        and texture.disposition
        is TextureHandlingDisposition.LOCAL_DETAIL_ENHANCEMENT_CANDIDATE
        and preflight.source_quality >= LOCAL_SR_SOURCE_QUALITY_MIN
        and preflight.compression_risk < LOCAL_SR_COMPRESSION_MAX
        and region_map.mean_confidence >= LOCAL_SR_REGION_CONFIDENCE_MIN
    )
    if local_candidate:
        confidence = (
            0.30 * preflight.source_quality
            + 0.20 * (1.0 - preflight.compression_risk)
            + 0.20 * region_map.mean_confidence
            + 0.15 * texture.confidence
            + 0.15 * min(1.0, LOCAL_SR_SCALE_MAX / max(1.0, scale_factor))
        )
        return SuperResolutionReadinessEvidence(
            disposition=SuperResolutionDisposition.LOCAL_SR_CANDIDATE,
            confidence=max(0.0, min(1.0, confidence)),
            fail_closed=False,
            reason_codes=["bounded_local_sr_benchmark_candidate"],
            missing_capabilities=["need_local_sr_benchmark"],
            **measured,
        )

    remote_candidate = (
        scale_factor <= REMOTE_SR_SCALE_MAX
        and preflight.source_quality >= REMOTE_SR_SOURCE_QUALITY_MIN
        and preflight.compression_risk < REMOTE_SR_COMPRESSION_MAX
        and region_map.mean_confidence >= REMOTE_SR_REGION_CONFIDENCE_MIN
    )
    if remote_candidate:
        confidence = (
            0.30 * preflight.source_quality
            + 0.20 * (1.0 - preflight.compression_risk)
            + 0.20 * region_map.mean_confidence
            + 0.15 * texture.confidence
            + 0.15 * min(1.0, REMOTE_SR_SCALE_MAX / max(1.0, scale_factor))
        )
        missing = ["need_remote_sr_benchmark"]
        reasons = ["bounded_remote_sr_benchmark_candidate"]
        fail_closed = False
        if not provider_available:
            fail_closed = True
            reasons.append("remote_sr_provider_unavailable")
            missing.append("need_remote_sr_provider")
        return SuperResolutionReadinessEvidence(
            disposition=SuperResolutionDisposition.REMOTE_SR_CANDIDATE,
            confidence=max(0.0, min(1.0, confidence)),
            fail_closed=fail_closed,
            reason_codes=reasons,
            missing_capabilities=sorted(set(missing)),
            **measured,
        )

    reasons = ["sr_readiness_evidence_inconclusive"]
    if native_long_edge < required_native_long_edge:
        reasons.append("native_resolution_below_qc_floor")
    if region_map.mean_confidence < REMOTE_SR_REGION_CONFIDENCE_MIN:
        reasons.append("regional_confidence_too_low_for_sr")
    if preflight.source_quality < REMOTE_SR_SOURCE_QUALITY_MIN:
        reasons.append("source_quality_below_remote_sr_candidate_floor")
    if preflight.compression_risk >= REMOTE_SR_COMPRESSION_MAX:
        reasons.append("compression_above_remote_sr_candidate_ceiling")

    return SuperResolutionReadinessEvidence(
        disposition=SuperResolutionDisposition.MANUAL_REVIEW,
        confidence=max(
            0.0,
            min(
                0.49,
                0.25 * preflight.source_quality
                + 0.20 * (1.0 - preflight.compression_risk)
                + 0.20 * region_map.mean_confidence,
            ),
        ),
        fail_closed=True,
        reason_codes=sorted(set(reasons)),
        missing_capabilities=[
            "need_higher_resolution_reference",
            "need_manual_review",
        ],
        **measured,
    )
