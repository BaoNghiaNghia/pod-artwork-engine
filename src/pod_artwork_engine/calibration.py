from __future__ import annotations

from typing import Callable

from .contracts import QualityMode
from .harness import HarnessStore
from .harness_models import (
    BenchmarkCaseResult,
    BenchmarkTier,
    QCPolicyCalibrationProposal,
    ThresholdCalibrationMetric,
)
from .qc_policy import QCModePolicy, QCPolicy


MetricExtractor = Callable[[BenchmarkCaseResult], float | None]


class QCPolicyCalibrator:
    """Generate a review-only QC policy proposal from benchmark evidence.

    The calibrator never changes the active policy path or production settings.
    It writes a candidate policy next to the proposal so a human can inspect it
    and explicitly choose to benchmark/use it later.
    """

    def __init__(self, store: HarnessStore, current_policy: QCPolicy) -> None:
        self.store = store
        self.current_policy = current_policy

    @staticmethod
    def _classify(
        result: BenchmarkCaseResult,
        *,
        good_quality_threshold: float,
        bad_quality_threshold: float,
    ) -> bool | None:
        if not result.success or result.quality_score is None:
            return False
        if result.quality_score >= good_quality_threshold:
            return True
        if result.quality_score <= bad_quality_threshold:
            return False
        return None

    @staticmethod
    def _evaluate_threshold(
        samples: list[tuple[float, bool]],
        threshold: float,
    ) -> tuple[float, float, int, int]:
        good = [value for value, label in samples if label]
        bad = [value for value, label in samples if not label]
        false_accepts = sum(value >= threshold for value in bad)
        false_rejects = sum(value < threshold for value in good)
        false_accept_rate = false_accepts / max(1, len(bad))
        false_reject_rate = false_rejects / max(1, len(good))
        return false_accept_rate, false_reject_rate, len(good), len(bad)

    def _calibrate_metric(
        self,
        name: str,
        results: list[BenchmarkCaseResult],
        extractor: MetricExtractor,
        *,
        current_threshold: float,
        good_quality_threshold: float,
        bad_quality_threshold: float,
        min_good: int,
        min_bad: int,
        max_false_accept_rate: float,
        max_threshold_delta: float,
    ) -> ThresholdCalibrationMetric:
        samples: list[tuple[float, bool]] = []
        for result in results:
            label = self._classify(
                result,
                good_quality_threshold=good_quality_threshold,
                bad_quality_threshold=bad_quality_threshold,
            )
            if label is None:
                continue
            value = extractor(result)
            if value is None:
                continue
            samples.append((value, label))

        good_count = sum(label for _, label in samples)
        bad_count = len(samples) - good_count
        base = ThresholdCalibrationMetric(
            metric=name,
            sample_count=len(samples),
            good_count=good_count,
            bad_count=bad_count,
            current_threshold=current_threshold,
        )
        if good_count < min_good or bad_count < min_bad:
            return base.model_copy(
                update={
                    "reasons": [
                        f"insufficient labeled evidence: good={good_count}, bad={bad_count}"
                    ]
                }
            )

        candidates = sorted(
            {
                round(value, 4)
                for value, _ in samples
            }
            | {round(current_threshold, 4)}
        )
        lower = max(0.0, current_threshold - max_threshold_delta)
        upper = min(1.0, current_threshold + max_threshold_delta)
        candidates = [
            max(lower, min(upper, candidate))
            for candidate in candidates
        ]
        candidates = sorted(set(round(value, 4) for value in candidates))

        ranked: list[tuple[float, float, float, float]] = []
        for threshold in candidates:
            false_accept_rate, false_reject_rate, _, _ = self._evaluate_threshold(
                samples,
                threshold,
            )
            if false_accept_rate > max_false_accept_rate:
                continue
            ranked.append(
                (
                    false_reject_rate,
                    abs(threshold - current_threshold),
                    -threshold,
                    threshold,
                )
            )

        if not ranked:
            return base.model_copy(
                update={
                    "reasons": [
                        "no threshold within the safety movement bound satisfies "
                        f"false-accept <= {max_false_accept_rate:.3f}"
                    ]
                }
            )

        ranked.sort()
        recommendation = ranked[0][3]
        false_accept_rate, false_reject_rate, _, _ = self._evaluate_threshold(
            samples,
            recommendation,
        )
        return base.model_copy(
            update={
                "recommended_threshold": recommendation,
                "false_accept_rate": false_accept_rate,
                "false_reject_rate": false_reject_rate,
                "sufficient_evidence": True,
                "reasons": [
                    "recommendation derived from labeled benchmark quality, "
                    "bounded around the current threshold"
                ],
            }
        )

    @staticmethod
    def _replace_mode_policy(
        policy: QCPolicy,
        mode: QualityMode,
        value: QCModePolicy,
    ) -> QCPolicy:
        field = {
            QualityMode.QUICK_2D: "quick_2d",
            QualityMode.PRINT_READY: "print_ready",
            QualityMode.MAX_FIDELITY: "max_fidelity",
        }[mode]
        return policy.model_copy(
            deep=True,
            update={
                field: value,
                "policy_id": f"{policy.policy_id}-calibration-candidate",
                "version": f"{policy.version}-candidate",
            },
        )

    def propose(
        self,
        run_ids: list[str],
        *,
        require_golden: bool = True,
        good_quality_threshold: float = 0.85,
        bad_quality_threshold: float = 0.70,
        min_good: int = 5,
        min_bad: int = 3,
        max_false_accept_rate: float = 0.05,
        max_threshold_delta: float = 0.10,
    ) -> QCPolicyCalibrationProposal:
        if not run_ids:
            raise ValueError("at least one benchmark run is required")
        if bad_quality_threshold >= good_quality_threshold:
            raise ValueError("bad quality threshold must be below good quality threshold")

        scorecards = []
        all_results: list[BenchmarkCaseResult] = []
        reasons: list[str] = []
        modes: set[QualityMode] = set()
        dataset_ids: set[str] = set()
        dataset_fingerprints: set[str] = set()
        source_policies: set[tuple[str, str]] = set()
        execution_kinds: set[str] = set()

        for run_id in run_ids:
            scorecard = self.store.get_scorecard(run_id)
            if scorecard is None:
                raise KeyError(f"scorecard not found: {run_id}")
            scorecards.append(scorecard)
            dataset_ids.add(scorecard.dataset_id)
            if scorecard.provenance.dataset_manifest_sha256:
                dataset_fingerprints.add(
                    scorecard.provenance.dataset_manifest_sha256
                )
            if scorecard.provenance.quality_mode is not None:
                modes.add(scorecard.provenance.quality_mode)
            if scorecard.provenance.execution_kind:
                execution_kinds.add(scorecard.provenance.execution_kind)
            if scorecard.provenance.qc_policy_id:
                source_policies.add(
                    (
                        scorecard.provenance.qc_policy_id,
                        scorecard.provenance.qc_policy_version,
                    )
                )
            all_results.extend(self.store.get_results(run_id))

        if len(dataset_ids) != 1:
            raise ValueError("calibration runs must use the same dataset")
        if len(dataset_fingerprints) > 1:
            raise ValueError(
                "calibration runs must use the same dataset manifest fingerprint"
            )
        if execution_kinds != {"production_engine"}:
            raise ValueError(
                "calibration requires production-engine Harness runs"
            )
        if len(source_policies) != 1:
            raise ValueError(
                "calibration runs must use one shared QC policy identity"
            )
        source_policy = next(iter(source_policies))
        if source_policy != (
            self.current_policy.policy_id,
            self.current_policy.version,
        ):
            raise ValueError(
                "calibration source QC policy does not match the current policy"
            )
        if len(modes) != 1:
            raise ValueError(
                "calibration requires production-engine runs with one shared quality mode"
            )
        mode = next(iter(modes))
        source_tiers = sorted(
            {scorecard.tier for scorecard in scorecards},
            key=lambda tier: tier.value,
        )
        if require_golden and any(
            scorecard.tier is not BenchmarkTier.GOLDEN for scorecard in scorecards
        ):
            reasons.append("safe calibration requires Golden Holdout runs by default")

        current_mode = self.current_policy.for_mode(mode)
        metric_specs: dict[str, tuple[MetricExtractor, float]] = {
            "semantic_min_score": (
                lambda result: result.runtime_qc.semantic_score,
                current_mode.semantic_min_score,
            ),
            "object_fidelity_min": (
                lambda result: result.runtime_qc.object_fidelity,
                current_mode.object_fidelity_min,
            ),
            "min_resolution_score": (
                lambda result: result.runtime_qc.resolution_score,
                current_mode.min_resolution_score,
            ),
        }

        metrics = {
            name: self._calibrate_metric(
                name,
                all_results,
                extractor,
                current_threshold=current_threshold,
                good_quality_threshold=good_quality_threshold,
                bad_quality_threshold=bad_quality_threshold,
                min_good=min_good,
                min_bad=min_bad,
                max_false_accept_rate=max_false_accept_rate,
                max_threshold_delta=max_threshold_delta,
            )
            for name, (extractor, current_threshold) in metric_specs.items()
        }

        proposed_mode = current_mode.model_copy(
            update={
                "semantic_min_score": (
                    metrics["semantic_min_score"].recommended_threshold
                    if metrics["semantic_min_score"].sufficient_evidence
                    else current_mode.semantic_min_score
                ),
                "object_fidelity_min": (
                    metrics["object_fidelity_min"].recommended_threshold
                    if metrics["object_fidelity_min"].sufficient_evidence
                    else current_mode.object_fidelity_min
                ),
                "min_resolution_score": (
                    metrics["min_resolution_score"].recommended_threshold
                    if metrics["min_resolution_score"].sufficient_evidence
                    else current_mode.min_resolution_score
                ),
            }
        )
        candidate_policy = self._replace_mode_policy(
            self.current_policy,
            mode,
            proposed_mode,
        )

        core_sufficient = (
            metrics["semantic_min_score"].sufficient_evidence
            and metrics["min_resolution_score"].sufficient_evidence
        )
        safe_source = not require_golden or not reasons
        sufficient = core_sufficient and safe_source

        if not core_sufficient:
            reasons.append(
                "semantic and resolution thresholds both need sufficient labeled evidence"
            )
        if not metrics["object_fidelity_min"].sufficient_evidence:
            reasons.append(
                "object-fidelity threshold retained because judge coverage is insufficient"
            )
        if sufficient:
            reasons.append(
                "proposal is evidence-backed but remains review-only and is not active"
            )

        proposal = QCPolicyCalibrationProposal(
            quality_mode=mode,
            source_run_ids=run_ids,
            source_tiers=source_tiers,
            current_policy=current_mode,
            proposed_policy=proposed_mode,
            candidate_policy=candidate_policy,
            metrics=metrics,
            good_quality_threshold=good_quality_threshold,
            bad_quality_threshold=bad_quality_threshold,
            sufficient_evidence=sufficient,
            requires_human_approval=True,
            automatically_applied=False,
            reasons=reasons,
        )

        output_dir = self.store.calibration_dir(proposal.proposal_id)
        self.store.save_model(output_dir / "proposal.json", proposal)
        self.store.save_model(
            output_dir / "candidate-qc-policy.json",
            candidate_policy,
        )
        return proposal
