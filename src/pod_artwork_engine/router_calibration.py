from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .contracts import ArtworkType
from .harness import HarnessStore
from .harness_models import (
    BenchmarkTier,
    RouteCaseComparison,
    RouteMatrixReport,
    RoutePreference,
    RouterPolicyCalibrationProposal,
    RouterThresholdCalibrationMetric,
)
from .router_policy import RouterPolicy


CasePredicate = Callable[[RouteCaseComparison], bool]


class RouterPolicyCalibrator:
    """Create review-only RouterPolicy proposals from counterfactual matrices.

    A matrix is valid evidence only when deterministic and remote-capable routes
    were evaluated on the same benchmark cases. The calibrator never activates
    the generated policy and never changes production settings.
    """

    def __init__(self, store: HarnessStore, current_policy: RouterPolicy) -> None:
        self.store = store
        self.current_policy = current_policy

    def _load_report(self, matrix_id: str) -> RouteMatrixReport:
        path = self.store.route_matrix_dir(matrix_id) / "report.json"
        if not path.is_file():
            raise KeyError(f"route matrix not found: {matrix_id}")
        return RouteMatrixReport.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _calibrate_threshold(
        name: str,
        comparisons: list[RouteCaseComparison],
        predicate: CasePredicate,
        *,
        current_threshold: float,
        min_remote: int,
        min_deterministic: int,
        max_false_local_rate: float,
        max_threshold_delta: float,
    ) -> RouterThresholdCalibrationMetric:
        samples: list[tuple[float, bool]] = []
        for item in comparisons:
            if (
                item.design_confidence is None
                or item.preference
                not in {RoutePreference.REMOTE, RoutePreference.DETERMINISTIC}
                or not predicate(item)
            ):
                continue
            samples.append(
                (
                    item.design_confidence,
                    item.preference is RoutePreference.REMOTE,
                )
            )

        prefer_remote = sum(label for _, label in samples)
        prefer_deterministic = len(samples) - prefer_remote
        base = RouterThresholdCalibrationMetric(
            metric=name,
            sample_count=len(samples),
            prefer_remote_count=prefer_remote,
            prefer_deterministic_count=prefer_deterministic,
            current_threshold=current_threshold,
        )

        if prefer_remote < min_remote or prefer_deterministic < min_deterministic:
            return base.model_copy(
                update={
                    "reasons": [
                        "insufficient counterfactual evidence: "
                        f"remote={prefer_remote}, deterministic={prefer_deterministic}"
                    ]
                }
            )

        lower = max(0.0, current_threshold - max_threshold_delta)
        upper = min(1.0, current_threshold + max_threshold_delta)
        candidate_values = {round(current_threshold, 4), round(lower, 4), round(upper, 4)}
        for confidence, _ in samples:
            candidate_values.add(round(max(lower, min(upper, confidence)), 4))
            candidate_values.add(
                round(max(lower, min(upper, confidence + 0.0001)), 4)
            )

        ranked: list[tuple[float, float, float, float]] = []
        for threshold in sorted(candidate_values):
            false_local = sum(
                label and confidence >= threshold
                for confidence, label in samples
            ) / prefer_remote
            unnecessary_remote = sum(
                (not label) and confidence < threshold
                for confidence, label in samples
            ) / prefer_deterministic

            if false_local > max_false_local_rate:
                continue

            ranked.append(
                (
                    unnecessary_remote,
                    abs(threshold - current_threshold),
                    false_local,
                    threshold,
                )
            )

        if not ranked:
            return base.model_copy(
                update={
                    "reasons": [
                        "no bounded threshold satisfies the configured false-local ceiling"
                    ]
                }
            )

        ranked.sort()
        recommendation = ranked[0][3]
        false_local = sum(
            label and confidence >= recommendation
            for confidence, label in samples
        ) / prefer_remote
        unnecessary_remote = sum(
            (not label) and confidence < recommendation
            for confidence, label in samples
        ) / prefer_deterministic

        return base.model_copy(
            update={
                "recommended_threshold": recommendation,
                "false_local_rate": false_local,
                "unnecessary_remote_rate": unnecessary_remote,
                "sufficient_evidence": True,
                "reasons": [
                    "threshold derived from deterministic-vs-remote counterfactual quality"
                ],
            }
        )

    def propose(
        self,
        matrix_ids: list[str],
        *,
        require_golden: bool = True,
        min_remote: int = 3,
        min_deterministic: int = 3,
        max_false_local_rate: float = 0.05,
        max_threshold_delta: float = 0.15,
    ) -> RouterPolicyCalibrationProposal:
        if not matrix_ids:
            raise ValueError("at least one route matrix is required")

        reports = [self._load_report(matrix_id) for matrix_id in matrix_ids]
        dataset_ids = {report.dataset_id for report in reports}
        fingerprints = {
            report.dataset_manifest_sha256
            for report in reports
            if report.dataset_manifest_sha256
        }
        quality_modes = {report.quality_mode for report in reports}
        policy_ids = {
            (report.router_policy_id, report.router_policy_version)
            for report in reports
        }
        min_quality_gains = {report.min_quality_gain for report in reports}
        reasons: list[str] = []

        if len(dataset_ids) != 1:
            raise ValueError("router calibration matrices must use the same dataset")
        if len(fingerprints) != 1:
            raise ValueError(
                "router calibration matrices require one shared dataset manifest fingerprint"
            )
        if len(quality_modes) != 1:
            raise ValueError(
                "router calibration matrices must use the same quality mode"
            )
        if len(min_quality_gains) != 1:
            raise ValueError(
                "router calibration matrices must use the same min_quality_gain"
            )
        if policy_ids != {
            (self.current_policy.policy_id, self.current_policy.version)
        }:
            raise ValueError(
                "route matrices were not produced with the current RouterPolicy"
            )

        if require_golden and any(
            report.tier is not BenchmarkTier.GOLDEN for report in reports
        ):
            reasons.append("safe router calibration requires Golden Holdout matrices")

        comparisons = [
            item
            for report in reports
            for item in report.comparisons
            if item.remote_used
        ]
        if not comparisons:
            reasons.append("no valid remote counterfactual evidence is available")

        no_semantic = lambda item: (
            "need_semantic_reconstruction" not in item.required_capabilities
        )
        text_logo = lambda item: (
            no_semantic(item)
            and item.artwork_type in {ArtworkType.TYPOGRAPHY, ArtworkType.LOGO}
        )
        clean_non_text = lambda item: (
            no_semantic(item)
            and item.artwork_type not in {ArtworkType.TYPOGRAPHY, ArtworkType.LOGO}
        )
        low_conf_semantic = lambda item: (
            "need_semantic_reconstruction" in item.required_capabilities
            and item.artwork_type not in {ArtworkType.ILLUSTRATION, ArtworkType.MIXED}
        )

        metrics = {
            "deterministic_text_logo_min_confidence": self._calibrate_threshold(
                "deterministic_text_logo_min_confidence",
                comparisons,
                text_logo,
                current_threshold=(
                    self.current_policy.deterministic_text_logo_min_confidence
                ),
                min_remote=min_remote,
                min_deterministic=min_deterministic,
                max_false_local_rate=max_false_local_rate,
                max_threshold_delta=max_threshold_delta,
            ),
            "clean_passthrough_min_confidence": self._calibrate_threshold(
                "clean_passthrough_min_confidence",
                comparisons,
                clean_non_text,
                current_threshold=self.current_policy.clean_passthrough_min_confidence,
                min_remote=min_remote,
                min_deterministic=min_deterministic,
                max_false_local_rate=max_false_local_rate,
                max_threshold_delta=max_threshold_delta,
            ),
            "low_confidence_remote_below": self._calibrate_threshold(
                "low_confidence_remote_below",
                comparisons,
                low_conf_semantic,
                current_threshold=self.current_policy.low_confidence_remote_below,
                min_remote=min_remote,
                min_deterministic=min_deterministic,
                max_false_local_rate=max_false_local_rate,
                max_threshold_delta=max_threshold_delta,
            ),
        }

        updates = {}
        for name, metric in metrics.items():
            if metric.sufficient_evidence and metric.recommended_threshold is not None:
                updates[name] = metric.recommended_threshold

        candidate = self.current_policy.model_copy(
            deep=True,
            update={
                **updates,
                "policy_id": f"{self.current_policy.policy_id}-calibration-candidate",
                "version": f"{self.current_policy.version}-candidate",
            },
        )

        safe_source = not require_golden or not any(
            "Golden Holdout" in reason for reason in reasons
        )
        sufficient = bool(updates) and safe_source
        if not updates:
            reasons.append(
                "no router threshold has enough deterministic-vs-remote evidence"
            )
        if sufficient:
            reasons.append(
                "candidate is review-only; benchmark it again before any production use"
            )

        proposal = RouterPolicyCalibrationProposal(
            source_matrix_ids=matrix_ids,
            source_tiers=sorted(
                {report.tier for report in reports},
                key=lambda tier: tier.value,
            ),
            current_policy=self.current_policy,
            candidate_policy=candidate,
            metrics=metrics,
            min_quality_gain=next(iter(min_quality_gains)),
            sufficient_evidence=sufficient,
            requires_human_approval=True,
            automatically_applied=False,
            reasons=reasons,
        )

        output_dir = self.store.router_calibration_dir(proposal.proposal_id)
        self.store.save_model(output_dir / "proposal.json", proposal)
        self.store.save_model(
            output_dir / "candidate-router-policy.json",
            candidate,
        )
        return proposal
