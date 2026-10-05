from __future__ import annotations

from .contracts import ArtworkType, DesignSpec, QualityMode, RouteDecision, RouteKind


def choose_route(
    design_spec: DesignSpec,
    quality_mode: QualityMode,
    *,
    remote_available: bool,
) -> RouteDecision:
    capabilities = set(design_spec.required_capabilities)
    reasons: list[str] = []

    deterministic_candidate = (
        design_spec.artwork_type in {ArtworkType.TYPOGRAPHY, ArtworkType.LOGO}
        and design_spec.confidence >= 0.50
    )
    clean_passthrough_candidate = (
        "need_semantic_reconstruction" not in capabilities
        and design_spec.confidence >= 0.65
    )

    if deterministic_candidate and "need_semantic_reconstruction" not in capabilities:
        reasons.append("high_confidence_text_or_logo")
        return RouteDecision(
            route=RouteKind.DETERMINISTIC,
            required_capabilities=sorted(capabilities),
            reason_codes=reasons,
            use_remote_provider=False,
            deterministic_finish=True,
        )

    if clean_passthrough_candidate:
        reasons.append("clean_source_no_semantic_reconstruction_needed")
        return RouteDecision(
            route=RouteKind.DETERMINISTIC,
            required_capabilities=sorted(capabilities),
            reason_codes=reasons,
            use_remote_provider=False,
            deterministic_finish=True,
        )

    if remote_available:
        if quality_mode is QualityMode.QUICK_2D:
            reasons.append("quick_2d_remote_first")
            return RouteDecision(
                route=RouteKind.REMOTE_SEMANTIC,
                required_capabilities=sorted(capabilities | {"need_fast_draft"}),
                reason_codes=reasons,
                use_remote_provider=True,
                deterministic_finish=True,
            )

        if design_spec.artwork_type in {ArtworkType.ILLUSTRATION, ArtworkType.MIXED}:
            reasons.append("complex_artwork_remote_semantic")
            return RouteDecision(
                route=RouteKind.HYBRID,
                required_capabilities=sorted(capabilities | {"need_semantic_reconstruction"}),
                reason_codes=reasons,
                use_remote_provider=True,
                deterministic_finish=True,
            )

        if design_spec.confidence < 0.50:
            reasons.append("low_analysis_confidence")
            return RouteDecision(
                route=RouteKind.HYBRID,
                required_capabilities=sorted(capabilities | {"need_semantic_reconstruction"}),
                reason_codes=reasons,
                use_remote_provider=True,
                deterministic_finish=True,
            )

    reasons.append("remote_unavailable_local_baseline")
    if design_spec.artwork_type in {ArtworkType.ILLUSTRATION, ArtworkType.MIXED}:
        reasons.append("semantic_quality_may_require_review")
    return RouteDecision(
        route=RouteKind.DETERMINISTIC,
        required_capabilities=sorted(capabilities),
        reason_codes=reasons,
        use_remote_provider=False,
        deterministic_finish=True,
    )
