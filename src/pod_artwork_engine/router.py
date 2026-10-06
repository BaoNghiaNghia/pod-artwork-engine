from __future__ import annotations

from .contracts import ArtworkType, DesignSpec, QualityMode, RouteDecision, RouteKind
from .router_policy import DEFAULT_ROUTER_POLICY, RouterPolicy


def choose_route(
    design_spec: DesignSpec,
    quality_mode: QualityMode,
    *,
    remote_available: bool,
    policy: RouterPolicy | None = None,
) -> RouteDecision:
    policy = policy or DEFAULT_ROUTER_POLICY
    capabilities = set(design_spec.required_capabilities)
    reasons: list[str] = []

    deterministic_candidate = (
        design_spec.artwork_type in {ArtworkType.TYPOGRAPHY, ArtworkType.LOGO}
        and design_spec.confidence >= policy.deterministic_text_logo_min_confidence
    )
    clean_passthrough_candidate = (
        "need_semantic_reconstruction" not in capabilities
        and design_spec.confidence >= policy.clean_passthrough_min_confidence
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
        if "need_reference_disambiguation" in capabilities:
            reasons.append("reference_conflict_remote_disambiguation")
            return RouteDecision(
                route=RouteKind.HYBRID,
                required_capabilities=sorted(
                    capabilities | {"need_semantic_reconstruction"}
                ),
                reason_codes=reasons,
                use_remote_provider=True,
                deterministic_finish=True,
            )

        if quality_mode is QualityMode.QUICK_2D and policy.quick_2d_remote_first:
            reasons.append("quick_2d_remote_first")
            return RouteDecision(
                route=RouteKind.REMOTE_SEMANTIC,
                required_capabilities=sorted(capabilities | {"need_fast_draft"}),
                reason_codes=reasons,
                use_remote_provider=True,
                deterministic_finish=True,
            )

        if (
            policy.remote_complex_enabled
            and design_spec.artwork_type in {ArtworkType.ILLUSTRATION, ArtworkType.MIXED}
        ):
            reasons.append("complex_artwork_remote_semantic")
            return RouteDecision(
                route=RouteKind.HYBRID,
                required_capabilities=sorted(
                    capabilities | {"need_semantic_reconstruction"}
                ),
                reason_codes=reasons,
                use_remote_provider=True,
                deterministic_finish=True,
            )

        if design_spec.confidence < policy.low_confidence_remote_below:
            reasons.append("low_analysis_confidence")
            return RouteDecision(
                route=RouteKind.HYBRID,
                required_capabilities=sorted(
                    capabilities | {"need_semantic_reconstruction"}
                ),
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


def forced_route_decision(
    design_spec: DesignSpec,
    route: RouteKind,
    *,
    remote_available: bool,
) -> RouteDecision:
    """Create a Harness-only counterfactual route decision.

    This helper does not change production policy. A forced remote route may
    still fall back locally if the configured provider is unavailable; Harness
    evidence records whether remote reconstruction actually executed.
    """
    capabilities = set(design_spec.required_capabilities)
    use_remote = route in {RouteKind.REMOTE_SEMANTIC, RouteKind.HYBRID}
    reasons = ["harness_route_override"]

    if use_remote:
        capabilities.add("need_semantic_reconstruction")
        if not remote_available:
            reasons.append("remote_unavailable_experiment_fallback")

    return RouteDecision(
        route=route,
        required_capabilities=sorted(capabilities),
        reason_codes=reasons,
        use_remote_provider=use_remote,
        deterministic_finish=True,
    )
