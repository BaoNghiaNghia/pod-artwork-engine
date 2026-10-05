from __future__ import annotations

from pathlib import Path

from pod_artwork_engine.contracts import ArtworkType, DesignSpec, QualityMode, RouteKind
from pod_artwork_engine.router import choose_route, forced_route_decision
from pod_artwork_engine.router_policy import RouterPolicy, load_router_policy


def test_router_policy_controls_confidence_thresholds() -> None:
    spec = DesignSpec(
        artwork_type=ArtworkType.LOGO,
        confidence=0.70,
    )
    strict = RouterPolicy(
        policy_id="strict",
        deterministic_text_logo_min_confidence=0.80,
        clean_passthrough_min_confidence=0.80,
        low_confidence_remote_below=0.75,
    )

    decision = choose_route(
        spec,
        QualityMode.PRINT_READY,
        remote_available=True,
        policy=strict,
    )

    assert decision.route is RouteKind.HYBRID
    assert decision.use_remote_provider is True
    assert "low_analysis_confidence" in decision.reason_codes


def test_harness_forced_route_is_explicit_and_does_not_claim_execution() -> None:
    spec = DesignSpec(
        artwork_type=ArtworkType.ILLUSTRATION,
        confidence=0.90,
    )

    decision = forced_route_decision(
        spec,
        RouteKind.HYBRID,
        remote_available=False,
    )

    assert decision.route is RouteKind.HYBRID
    assert decision.use_remote_provider is True
    assert "harness_route_override" in decision.reason_codes
    assert "remote_unavailable_experiment_fallback" in decision.reason_codes


def test_router_policy_file_is_strictly_loaded(tmp_path: Path) -> None:
    path = tmp_path / "router-policy.json"
    policy = RouterPolicy(
        policy_id="fixture",
        version="2",
        clean_passthrough_min_confidence=0.72,
    )
    path.write_text(policy.model_dump_json(indent=2), encoding="utf-8")

    loaded = load_router_policy(path)

    assert loaded.policy_id == "fixture"
    assert loaded.version == "2"
    assert loaded.clean_passthrough_min_confidence == 0.72
