from __future__ import annotations

from .contracts import (
    ArtworkType,
    DesignSpec,
    RepresentationComponentKind,
    RepresentationComponentPlan,
    RepresentationKind,
    RepresentationPlanEvidence,
)


TYPOGRAPHY_LAYOUT_MIN = 0.75
TYPOGRAPHY_FONT_MIN = 0.70
TYPOGRAPHY_LINE_MIN = 0.70
GEOMETRY_MIN = 0.80
GEOMETRY_PRIMITIVE_MIN = 0.70


def _typography_component(
    design_spec: DesignSpec,
) -> RepresentationComponentPlan | None:
    typography = design_spec.typography
    expects_typography = bool(
        design_spec.exact_text
        or typography is not None
        or design_spec.artwork_type is ArtworkType.TYPOGRAPHY
        or "need_exact_text" in design_spec.required_capabilities
    )
    if not expects_typography:
        return None

    missing: list[str] = []
    reasons: list[str] = []

    if typography is None or not typography.lines:
        missing.append("need_typography_layout")
        reasons.append("typography_layout_unavailable")
        return RepresentationComponentPlan(
            component=RepresentationComponentKind.TYPOGRAPHY,
            representation=RepresentationKind.RASTER,
            confidence=max(0.0, min(1.0, design_spec.confidence * 0.55)),
            supported=True,
            reason_codes=reasons,
            missing_capabilities=missing,
        )

    line_text = [line.text for line in typography.lines]
    exact_text_verified = (
        not design_spec.exact_text
        or line_text == design_spec.exact_text
    )
    if not exact_text_verified:
        missing.append("need_exact_text_verification")
        reasons.append("exact_text_not_verified")

    if typography.line_order_confidence < TYPOGRAPHY_LAYOUT_MIN:
        missing.append("need_typography_layout_verification")
        reasons.append("typography_layout_low_confidence")

    line_confidence = min(
        (line.confidence for line in typography.lines),
        default=0.0,
    )
    if line_confidence < TYPOGRAPHY_LINE_MIN:
        missing.append("need_typography_verification")
        reasons.append("typography_line_low_confidence")

    font_names_present = all(bool(line.font_family.strip()) for line in typography.lines)
    if not font_names_present:
        missing.append("need_font_identification")
        reasons.append("font_family_unresolved")

    accepted_font_matches = all(
        line.font_match is not None and line.font_match.accepted
        for line in typography.lines
    )
    if (
        typography.font_match_confidence < TYPOGRAPHY_FONT_MIN
        or not accepted_font_matches
    ):
        missing.append("need_font_verification")
        reasons.append(
            "font_match_low_confidence"
            if typography.font_match_confidence < TYPOGRAPHY_FONT_MIN
            else "font_match_not_accepted"
        )

    vector_ready = not missing
    if vector_ready:
        reasons.append("verified_typography_vector_candidate")
        confidence = min(
            design_spec.confidence,
            typography.line_order_confidence,
            typography.font_match_confidence,
            line_confidence,
        )
        representation = RepresentationKind.VECTOR
    else:
        confidence = min(
            design_spec.confidence,
            max(
                0.0,
                typography.line_order_confidence,
                line_confidence,
            ),
        )
        representation = RepresentationKind.RASTER

    return RepresentationComponentPlan(
        component=RepresentationComponentKind.TYPOGRAPHY,
        representation=representation,
        confidence=max(0.0, min(1.0, confidence)),
        supported=True,
        reason_codes=sorted(set(reasons)),
        missing_capabilities=sorted(set(missing)),
    )


def _geometry_component(
    design_spec: DesignSpec,
) -> RepresentationComponentPlan | None:
    geometry = design_spec.geometry
    expects_geometry = bool(
        geometry is not None
        or design_spec.artwork_type is ArtworkType.LOGO
        or "need_vector" in design_spec.required_capabilities
    )
    if not expects_geometry:
        return None

    missing: list[str] = []
    reasons: list[str] = []

    if geometry is None or not geometry.primitives:
        missing.append("need_vector_geometry")
        reasons.append("geometry_evidence_unavailable")
        return RepresentationComponentPlan(
            component=RepresentationComponentKind.GEOMETRY,
            representation=RepresentationKind.RASTER,
            confidence=max(0.0, min(1.0, design_spec.confidence * 0.55)),
            supported=True,
            reason_codes=reasons,
            missing_capabilities=missing,
        )

    primitive_confidence = min(
        (primitive.confidence for primitive in geometry.primitives),
        default=0.0,
    )
    if geometry.confidence < GEOMETRY_MIN:
        missing.append("need_geometry_verification")
        reasons.append("geometry_low_confidence")
    if primitive_confidence < GEOMETRY_PRIMITIVE_MIN:
        missing.append("need_geometry_verification")
        reasons.append("geometry_primitive_low_confidence")

    if not missing:
        reasons.append("verified_geometry_vector_candidate")
        representation = RepresentationKind.VECTOR
        confidence = min(
            design_spec.confidence,
            geometry.confidence,
            primitive_confidence,
        )
    else:
        representation = RepresentationKind.RASTER
        confidence = min(
            design_spec.confidence,
            max(geometry.confidence, primitive_confidence),
        )

    return RepresentationComponentPlan(
        component=RepresentationComponentKind.GEOMETRY,
        representation=representation,
        confidence=max(0.0, min(1.0, confidence)),
        supported=True,
        reason_codes=sorted(set(reasons)),
        missing_capabilities=sorted(set(missing)),
    )


def _illustration_component(
    design_spec: DesignSpec,
) -> RepresentationComponentPlan | None:
    has_raster_semantics = bool(
        design_spec.artwork_type in {ArtworkType.ILLUSTRATION, ArtworkType.MIXED}
        or design_spec.texture_classes
        or design_spec.objects
        or "need_semantic_reconstruction" in design_spec.required_capabilities
    )
    if not has_raster_semantics:
        return None

    reasons = ["illustration_texture_preserved_as_raster"]
    if "fine_detail" in design_spec.texture_classes:
        reasons.append("fine_detail_raster_preservation")
    if design_spec.occlusion > 0:
        reasons.append("occlusion_requires_raster_or_semantic_evidence")
    if design_spec.perspective_severity > 0:
        reasons.append("perspective_requires_raster_or_semantic_evidence")

    confidence = design_spec.confidence
    if "need_semantic_reconstruction" in design_spec.required_capabilities:
        confidence = min(confidence, 0.75)

    return RepresentationComponentPlan(
        component=RepresentationComponentKind.ILLUSTRATION_TEXTURE,
        representation=RepresentationKind.RASTER,
        confidence=max(0.0, min(1.0, confidence)),
        supported=True,
        reason_codes=sorted(set(reasons)),
    )


def build_representation_plan(
    design_spec: DesignSpec,
) -> RepresentationPlanEvidence:
    components: list[RepresentationComponentPlan] = []

    typography = _typography_component(design_spec)
    if typography is not None:
        components.append(typography)

    geometry = _geometry_component(design_spec)
    if geometry is not None:
        components.append(geometry)

    illustration = _illustration_component(design_spec)
    if illustration is not None:
        components.append(illustration)

    fail_closed = False
    plan_reasons: list[str] = []
    missing_capabilities: list[str] = []

    if not components:
        fail_closed = True
        plan_reasons.append("content_representation_unknown")
        missing_capabilities.append("need_content_classification")
        components.append(
            RepresentationComponentPlan(
                component=RepresentationComponentKind.UNKNOWN_CONTENT,
                representation=RepresentationKind.RASTER,
                confidence=max(0.0, min(1.0, design_spec.confidence * 0.5)),
                supported=True,
                reason_codes=["unknown_content_preserved_as_raster"],
                missing_capabilities=["need_content_classification"],
            )
        )

    for component in components:
        missing_capabilities.extend(component.missing_capabilities)

    if missing_capabilities:
        fail_closed = True
        plan_reasons.append("vectorization_incomplete_fallback_to_raster")

    vector_components = sum(
        component.representation is RepresentationKind.VECTOR
        for component in components
    )
    raster_components = sum(
        component.representation is RepresentationKind.RASTER
        for component in components
    )

    if vector_components and raster_components:
        overall = RepresentationKind.HYBRID
        plan_reasons.append("mixed_vector_raster_components")
    elif vector_components:
        overall = RepresentationKind.VECTOR
        plan_reasons.append("all_components_vector_ready")
    else:
        overall = RepresentationKind.RASTER
        plan_reasons.append("raster_preservation_required")

    confidence = sum(component.confidence for component in components) / max(
        1,
        len(components),
    )

    return RepresentationPlanEvidence(
        overall=overall,
        confidence=max(0.0, min(1.0, confidence)),
        fail_closed=fail_closed,
        components=components,
        vector_components=vector_components,
        raster_components=raster_components,
        reason_codes=sorted(set(plan_reasons)),
        missing_capabilities=sorted(set(missing_capabilities)),
    )
