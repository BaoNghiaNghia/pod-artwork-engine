from __future__ import annotations

from .contracts import (
    BoundingBox,
    MultiReferenceAlignmentEvidence,
    MultiReferenceFusionEvidence,
    NormalizedPoint,
    PreflightResult,
    ReferenceAlignmentDisposition,
    ReferenceAlignmentEvidence,
    ReferenceEvidence,
    ReferenceEvidenceStatus,
)


AFFINE_ASPECT_COMPATIBILITY_MIN = 0.94
HOMOGRAPHY_ASPECT_COMPATIBILITY_MIN = 0.78
MIN_ARTWORK_CONFIDENCE = 0.60
HOMOGRAPHY_PERSPECTIVE_THRESHOLD = 0.12
SEMANTIC_PERSPECTIVE_THRESHOLD = 0.30
SEMANTIC_OCCLUSION_THRESHOLD = 0.10


def _quad(bbox: BoundingBox) -> list[NormalizedPoint]:
    right = min(1.0, bbox.x + bbox.width)
    bottom = min(1.0, bbox.y + bbox.height)
    return [
        NormalizedPoint(x=bbox.x, y=bbox.y),
        NormalizedPoint(x=right, y=bbox.y),
        NormalizedPoint(x=right, y=bottom),
        NormalizedPoint(x=bbox.x, y=bottom),
    ]


def _aspect_compatibility(
    source: PreflightResult,
    target: PreflightResult,
) -> float:
    if source.artwork_bbox is None or target.artwork_bbox is None:
        return 0.0
    source_width = max(1e-6, source.width * source.artwork_bbox.width)
    source_height = max(1e-6, source.height * source.artwork_bbox.height)
    target_width = max(1e-6, target.width * target.artwork_bbox.width)
    target_height = max(1e-6, target.height * target.artwork_bbox.height)
    source_ratio = source_width / source_height
    target_ratio = target_width / target_height
    return min(source_ratio, target_ratio) / max(source_ratio, target_ratio, 1e-6)


def _affine_matrix(source_bbox: BoundingBox, target_bbox: BoundingBox) -> list[float]:
    scale_x = target_bbox.width / max(source_bbox.width, 1e-6)
    scale_y = target_bbox.height / max(source_bbox.height, 1e-6)
    translate_x = target_bbox.x - scale_x * source_bbox.x
    translate_y = target_bbox.y - scale_y * source_bbox.y
    return [
        round(scale_x, 12),
        0.0,
        round(translate_x, 12),
        0.0,
        round(scale_y, 12),
        round(translate_y, 12),
        0.0,
        0.0,
        1.0,
    ]


def _reference_lookup(
    fusion: MultiReferenceFusionEvidence,
) -> dict[int, ReferenceEvidence]:
    return {item.index: item for item in fusion.references}


def build_reference_alignment(
    preflights: list[PreflightResult],
    fusion: MultiReferenceFusionEvidence,
    *,
    perspective_severity: list[float] | None = None,
    occlusion: list[float] | None = None,
) -> MultiReferenceAlignmentEvidence:
    if len(preflights) != fusion.reference_count:
        raise ValueError(
            "reference alignment requires preflights matching fusion reference_count"
        )
    if not 0 <= fusion.primary_index < len(preflights):
        raise ValueError("reference alignment primary index is out of range")

    perspective_values = perspective_severity or [0.0] * len(preflights)
    occlusion_values = occlusion or [0.0] * len(preflights)
    if len(perspective_values) != len(preflights):
        raise ValueError("perspective severity list must match preflight count")
    if len(occlusion_values) != len(preflights):
        raise ValueError("occlusion list must match preflight count")

    primary = preflights[fusion.primary_index]
    primary_bbox = primary.artwork_bbox
    lookup = _reference_lookup(fusion)
    references: list[ReferenceAlignmentEvidence] = []
    aligned_indices: list[int] = []
    excluded_indices: list[int] = []
    confidences: list[float] = []
    aggregate_reasons: list[str] = []

    affine_count = 0
    homography_count = 0
    semantic_count = 0
    manual_count = 0

    for index, preflight in enumerate(preflights):
        bbox = preflight.artwork_bbox
        perspective = max(0.0, min(1.0, float(perspective_values[index])))
        hidden = max(0.0, min(1.0, float(occlusion_values[index])))
        fusion_ref = lookup.get(index)
        status = (
            fusion_ref.status
            if fusion_ref is not None
            else ReferenceEvidenceStatus.AMBIGUOUS
        )
        similarity = (
            float(fusion_ref.similarity_to_primary)
            if fusion_ref is not None
            else 0.0
        )

        if index == fusion.primary_index:
            confidence = primary.artwork_confidence
            if primary_bbox is None:
                references.append(
                    ReferenceAlignmentEvidence(
                        index=index,
                        disposition=ReferenceAlignmentDisposition.MANUAL_REVIEW,
                        source_bbox=None,
                        target_bbox=None,
                        geometry_confidence=confidence,
                        aspect_compatibility=0.0,
                        perspective_severity=perspective,
                        occlusion=hidden,
                        fail_closed=True,
                        reason_codes=["primary_artwork_bbox_unavailable"],
                        missing_capabilities=["need_artwork_localization"],
                    )
                )
                excluded_indices.append(index)
                manual_count += 1
                aggregate_reasons.append("primary_artwork_bbox_unavailable")
            else:
                references.append(
                    ReferenceAlignmentEvidence(
                        index=index,
                        disposition=ReferenceAlignmentDisposition.IDENTITY,
                        source_bbox=primary_bbox,
                        target_bbox=primary_bbox,
                        source_quad=_quad(primary_bbox),
                        target_quad=_quad(primary_bbox),
                        transform_matrix=[
                            1.0, 0.0, 0.0,
                            0.0, 1.0, 0.0,
                            0.0, 0.0, 1.0,
                        ],
                        geometry_confidence=confidence,
                        aspect_compatibility=1.0,
                        perspective_severity=perspective,
                        occlusion=hidden,
                        fail_closed=False,
                        reason_codes=["primary_reference_identity"],
                    )
                )
                aligned_indices.append(index)
                confidences.append(confidence)
            continue

        if status is not ReferenceEvidenceStatus.CONSISTENT:
            reason = (
                "conflicting_reference_excluded"
                if status is ReferenceEvidenceStatus.CONFLICTING
                else "ambiguous_reference_excluded"
            )
            references.append(
                ReferenceAlignmentEvidence(
                    index=index,
                    disposition=ReferenceAlignmentDisposition.MANUAL_REVIEW,
                    source_bbox=bbox,
                    target_bbox=primary_bbox,
                    source_quad=_quad(bbox) if bbox else [],
                    target_quad=_quad(primary_bbox) if primary_bbox else [],
                    geometry_confidence=0.0,
                    aspect_compatibility=(
                        _aspect_compatibility(preflight, primary)
                        if bbox is not None and primary_bbox is not None
                        else 0.0
                    ),
                    perspective_severity=perspective,
                    occlusion=hidden,
                    fail_closed=True,
                    reason_codes=[reason],
                    missing_capabilities=["need_reference_disambiguation"],
                )
            )
            excluded_indices.append(index)
            manual_count += 1
            aggregate_reasons.append(reason)
            continue

        if bbox is None or primary_bbox is None:
            references.append(
                ReferenceAlignmentEvidence(
                    index=index,
                    disposition=ReferenceAlignmentDisposition.MANUAL_REVIEW,
                    source_bbox=bbox,
                    target_bbox=primary_bbox,
                    perspective_severity=perspective,
                    occlusion=hidden,
                    fail_closed=True,
                    reason_codes=["artwork_bbox_unavailable"],
                    missing_capabilities=["need_artwork_localization"],
                )
            )
            excluded_indices.append(index)
            manual_count += 1
            aggregate_reasons.append("artwork_bbox_unavailable")
            continue

        aspect = _aspect_compatibility(preflight, primary)
        base_confidence = max(
            0.0,
            min(
                1.0,
                0.40 * min(preflight.artwork_confidence, primary.artwork_confidence)
                + 0.35 * similarity
                + 0.25 * aspect,
            ),
        )

        if (
            preflight.artwork_confidence < MIN_ARTWORK_CONFIDENCE
            or primary.artwork_confidence < MIN_ARTWORK_CONFIDENCE
        ):
            references.append(
                ReferenceAlignmentEvidence(
                    index=index,
                    disposition=ReferenceAlignmentDisposition.MANUAL_REVIEW,
                    source_bbox=bbox,
                    target_bbox=primary_bbox,
                    source_quad=_quad(bbox),
                    target_quad=_quad(primary_bbox),
                    geometry_confidence=base_confidence,
                    aspect_compatibility=aspect,
                    perspective_severity=perspective,
                    occlusion=hidden,
                    fail_closed=True,
                    reason_codes=["artwork_localization_low_confidence"],
                    missing_capabilities=["need_artwork_localization"],
                )
            )
            excluded_indices.append(index)
            manual_count += 1
            aggregate_reasons.append("artwork_localization_low_confidence")
            continue

        if (
            perspective >= SEMANTIC_PERSPECTIVE_THRESHOLD
            or hidden >= SEMANTIC_OCCLUSION_THRESHOLD
            or aspect < HOMOGRAPHY_ASPECT_COMPATIBILITY_MIN
        ):
            reasons = []
            if perspective >= SEMANTIC_PERSPECTIVE_THRESHOLD:
                reasons.append("perspective_too_severe_for_bbox_alignment")
            if hidden >= SEMANTIC_OCCLUSION_THRESHOLD:
                reasons.append("occlusion_requires_semantic_registration")
            if aspect < HOMOGRAPHY_ASPECT_COMPATIBILITY_MIN:
                reasons.append("artwork_aspect_incompatible")
            references.append(
                ReferenceAlignmentEvidence(
                    index=index,
                    disposition=ReferenceAlignmentDisposition.SEMANTIC_REQUIRED,
                    source_bbox=bbox,
                    target_bbox=primary_bbox,
                    source_quad=_quad(bbox),
                    target_quad=_quad(primary_bbox),
                    geometry_confidence=base_confidence,
                    aspect_compatibility=aspect,
                    perspective_severity=perspective,
                    occlusion=hidden,
                    fail_closed=True,
                    reason_codes=reasons,
                    missing_capabilities=[
                        "need_semantic_registration",
                        "need_geometric_correspondence",
                    ],
                )
            )
            excluded_indices.append(index)
            semantic_count += 1
            aggregate_reasons.extend(reasons)
            continue

        if (
            perspective >= HOMOGRAPHY_PERSPECTIVE_THRESHOLD
            or aspect < AFFINE_ASPECT_COMPATIBILITY_MIN
        ):
            reasons = ["bbox_correspondence_insufficient_for_projective_alignment"]
            if perspective >= HOMOGRAPHY_PERSPECTIVE_THRESHOLD:
                reasons.append("perspective_homography_candidate")
            if aspect < AFFINE_ASPECT_COMPATIBILITY_MIN:
                reasons.append("aspect_shift_homography_candidate")
            references.append(
                ReferenceAlignmentEvidence(
                    index=index,
                    disposition=ReferenceAlignmentDisposition.HOMOGRAPHY_CANDIDATE,
                    source_bbox=bbox,
                    target_bbox=primary_bbox,
                    source_quad=_quad(bbox),
                    target_quad=_quad(primary_bbox),
                    geometry_confidence=base_confidence,
                    aspect_compatibility=aspect,
                    perspective_severity=perspective,
                    occlusion=hidden,
                    reprojection_error=None,
                    fail_closed=True,
                    reason_codes=reasons,
                    missing_capabilities=[
                        "need_feature_correspondence",
                        "need_homography_benchmark",
                    ],
                )
            )
            excluded_indices.append(index)
            homography_count += 1
            aggregate_reasons.extend(reasons)
            continue

        references.append(
            ReferenceAlignmentEvidence(
                index=index,
                disposition=ReferenceAlignmentDisposition.AFFINE_CANDIDATE,
                source_bbox=bbox,
                target_bbox=primary_bbox,
                source_quad=_quad(bbox),
                target_quad=_quad(primary_bbox),
                transform_matrix=_affine_matrix(bbox, primary_bbox),
                geometry_confidence=base_confidence,
                aspect_compatibility=aspect,
                perspective_severity=perspective,
                occlusion=hidden,
                reprojection_error=None,
                fail_closed=False,
                reason_codes=[
                    "bbox_scale_translate_candidate",
                    "bbox_correspondence_only",
                ],
                missing_capabilities=["need_affine_benchmark"],
            )
        )
        aligned_indices.append(index)
        confidences.append(base_confidence)
        affine_count += 1

    mean_confidence = (
        sum(confidences) / len(confidences)
        if confidences
        else 0.0
    )
    fail_closed = bool(
        homography_count
        or semantic_count
        or manual_count
        or fusion.conflict_detected
    )
    reason_codes = list(dict.fromkeys(aggregate_reasons))
    if len(preflights) == 1:
        reason_codes.append("single_reference_identity_only")
    if affine_count:
        reason_codes.append("affine_candidates_are_evidence_only")
    if homography_count:
        reason_codes.append("homography_execution_disabled")

    return MultiReferenceAlignmentEvidence(
        reference_count=len(preflights),
        primary_index=fusion.primary_index,
        mean_geometry_confidence=max(0.0, min(1.0, mean_confidence)),
        aligned_indices=sorted(aligned_indices),
        excluded_indices=sorted(set(excluded_indices)),
        affine_candidate_count=affine_count,
        homography_candidate_count=homography_count,
        semantic_required_count=semantic_count,
        manual_review_count=manual_count,
        fail_closed=fail_closed,
        execution_enabled=False,
        references=sorted(references, key=lambda item: item.index),
        reason_codes=reason_codes,
    )
