from __future__ import annotations

from pathlib import Path

from .harness import HarnessRunner, HarnessStore, load_candidate_manifest, load_recipe
from .harness_engine import HarnessEngineRunner
from .harness_models import (
    BenchmarkCaseResult,
    BenchmarkRecipe,
    BenchmarkScorecard,
    HarnessRunStatus,
    RunProvenance,
    SRBenchmarkLane,
    SRBenchmarkRecommendation,
    SRBenchmarkReport,
    SRBenchmarkSpec,
    SRCaseComparison,
    SRCasePreference,
    SRLaneCaseEvidence,
    SRLaneRun,
)
from .settings import Settings


class SRBenchmarkMatrixRunner:
    """Compare native output against externally supplied SR candidate lanes.

    The native lane can run the real production engine or consume an explicit
    CandidateManifest. Local/remote SR lanes never execute inside this runner:
    they must be supplied through CandidateManifest adapters produced by a
    concrete benchmark backend. Missing backends remain unavailable evidence.
    """

    def __init__(self, settings: Settings, registry, store: HarnessStore) -> None:
        self.settings = settings
        self.registry = registry
        self.store = store

    @staticmethod
    def _resolve(path: str | None, base: Path | None) -> Path | None:
        if path is None:
            return None
        resolved = Path(path).expanduser()
        if not resolved.is_absolute() and base is not None:
            resolved = base / resolved
        return resolved.resolve()

    @staticmethod
    def _recipe_for_lane(
        base: BenchmarkRecipe,
        lane: SRBenchmarkLane,
    ) -> BenchmarkRecipe:
        metadata = dict(base.metadata)
        metadata["harness_sr_lane"] = lane.value
        metadata["sr_benchmark_only"] = True
        return base.model_copy(
            deep=True,
            update={
                "recipe_id": f"{base.recipe_id}__sr_{lane.value}",
                "metadata": metadata,
            },
        )

    @staticmethod
    def _mean(values: list[float | None]) -> float | None:
        available = [value for value in values if value is not None]
        return sum(available) / len(available) if available else None

    @classmethod
    def _lane_run(
        cls,
        lane: SRBenchmarkLane,
        scorecard: BenchmarkScorecard,
        results: list[BenchmarkCaseResult],
    ) -> SRLaneRun:
        return SRLaneRun(
            lane=lane,
            available=True,
            run_id=scorecard.run_id,
            scorecard_id=scorecard.scorecard_id,
            status=scorecard.status,
            case_count=scorecard.case_count,
            success_count=scorecard.success_count,
            quality_mean=scorecard.quality_mean,
            technical_mean=scorecard.technical_mean,
            small_detail_survival_mean=cls._mean(
                [result.technical.small_detail_survival for result in results]
            ),
            latency_p50_ms=scorecard.latency_p50_ms,
            latency_p95_ms=scorecard.latency_p95_ms,
            peak_ram_mb=max(
                (result.operational.peak_ram_mb for result in results),
                default=0.0,
            ),
            peak_vram_mb=max(
                (result.operational.peak_vram_mb for result in results),
                default=0.0,
            ),
            provider_calls=scorecard.provider_calls,
            total_cost_usd=scorecard.total_cost_usd,
            manual_review_count=scorecard.manual_review_count,
        )

    @staticmethod
    def _unavailable_run(
        lane: SRBenchmarkLane,
        reason: str,
    ) -> SRLaneRun:
        return SRLaneRun(
            lane=lane,
            available=False,
            unavailable_reason=reason,
        )

    def _run_manifest_lane(
        self,
        *,
        lane: SRBenchmarkLane,
        spec: SRBenchmarkSpec,
        recipe: BenchmarkRecipe,
        manifest_path: Path,
    ) -> tuple[BenchmarkScorecard, list[BenchmarkCaseResult]]:
        if not manifest_path.is_file():
            raise FileNotFoundError(f"candidate manifest not found: {manifest_path}")
        manifest = load_candidate_manifest(manifest_path)
        provenance = RunProvenance(
            execution_kind=f"sr_benchmark_manifest:{lane.value}",
            quality_mode=spec.quality_mode,
        )
        scorecard = HarnessRunner(self.registry, self.store).run_candidates(
            spec.dataset_id,
            spec.tier,
            recipe,
            manifest,
            manifest_base=manifest_path.parent,
            limit=spec.limit,
            provenance=provenance,
        )
        return scorecard, self.store.get_results(scorecard.run_id)

    @staticmethod
    def _base_evidence(
        lane: SRBenchmarkLane,
        result: BenchmarkCaseResult | None,
    ) -> SRLaneCaseEvidence:
        if result is None:
            return SRLaneCaseEvidence(
                lane=lane,
                success=False,
                reasons=["case unavailable in lane"],
            )

        reasons: list[str] = []
        if result.error:
            reasons.append(result.error)
        failure_reason = result.metadata.get("failure_reason")
        if isinstance(failure_reason, str) and failure_reason.strip():
            reasons.append(failure_reason.strip())
        for key in ("sr_reason_codes", "reason_codes"):
            raw_reasons = result.metadata.get(key)
            if isinstance(raw_reasons, list):
                reasons.extend(
                    str(reason).strip()
                    for reason in raw_reasons
                    if str(reason).strip()
                )
        fail_closed = (
            result.precision.super_resolution_fail_closed
            or result.metadata.get("fail_closed") is True
        )
        if fail_closed:
            reasons.append("sr_readiness_fail_closed")
        if result.operational.manual_review:
            reasons.append("manual_review_required")
        hallucination_risk = result.metadata.get("hallucination_risk") is True

        return SRLaneCaseEvidence(
            lane=lane,
            success=result.success and result.quality_score is not None,
            quality_score=result.quality_score,
            semantic_score=result.semantic_score,
            technical_score=result.technical_score,
            small_detail_survival=result.technical.small_detail_survival,
            effective_resolution=result.technical.effective_resolution,
            latency_ms=result.operational.latency_ms,
            peak_ram_mb=result.operational.peak_ram_mb,
            peak_vram_mb=result.operational.peak_vram_mb,
            provider_calls=result.operational.provider_calls,
            cost_usd=result.operational.cost_usd,
            manual_review=result.operational.manual_review,
            fail_closed=fail_closed,
            hallucination_risk=hallucination_risk,
            reasons=list(dict.fromkeys(reasons)),
        )

    @staticmethod
    def _cohort_signature(
        result: BenchmarkCaseResult,
    ) -> tuple[str, str] | None:
        raw = result.metadata.get("sr_cohort")
        if not isinstance(raw, dict):
            return None
        cohort_id = raw.get("cohort_id")
        source_sha = raw.get("source_input_sha256")
        if not isinstance(cohort_id, str) or not cohort_id:
            return None
        if not isinstance(source_sha, str) or not source_sha:
            return None
        return cohort_id, source_sha

    @classmethod
    def _same_pre_sr_source(
        cls,
        baseline: BenchmarkCaseResult,
        challenger: BenchmarkCaseResult,
    ) -> bool:
        baseline_signature = cls._cohort_signature(baseline)
        challenger_signature = cls._cohort_signature(challenger)
        if baseline_signature is None and challenger_signature is None:
            return True
        return (
            baseline_signature is not None
            and challenger_signature is not None
            and baseline_signature == challenger_signature
        )

    @staticmethod
    def _hallucination_risk(
        native: BenchmarkCaseResult,
        challenger: BenchmarkCaseResult,
        *,
        max_semantic_drop: float,
    ) -> bool:
        if challenger.metadata.get("hallucination_risk") is True:
            return True
        if (
            native.semantic.exact_text is not None
            and challenger.semantic.exact_text is not None
            and challenger.semantic.exact_text < native.semantic.exact_text
        ):
            return True
        if (
            native.semantic.object_fidelity is not None
            and challenger.semantic.object_fidelity is not None
            and (
                native.semantic.object_fidelity
                - challenger.semantic.object_fidelity
            )
            > max_semantic_drop
        ):
            return True
        if (
            native.semantic_score is not None
            and challenger.semantic_score is not None
            and native.semantic_score - challenger.semantic_score
            > max_semantic_drop
        ):
            return True
        return False

    @staticmethod
    def _challenger_qualifies(
        native: BenchmarkCaseResult,
        challenger: BenchmarkCaseResult,
        spec: SRBenchmarkSpec,
    ) -> tuple[bool, float | None, float | None, float | None, list[str]]:
        reasons: list[str] = []
        if not SRBenchmarkMatrixRunner._same_pre_sr_source(native, challenger):
            reasons.append("pre-SR cohort source mismatch")
        if not challenger.success or challenger.quality_score is None:
            reasons.append("challenger result unavailable")
            return False, None, None, None, reasons
        if challenger.operational.manual_review:
            reasons.append("challenger requires manual review")
        if challenger.precision.super_resolution_fail_closed:
            reasons.append("challenger SR evidence is fail-closed")

        quality_delta = (
            challenger.quality_score - native.quality_score
            if native.quality_score is not None
            else None
        )
        if quality_delta is None or quality_delta < spec.min_quality_gain:
            reasons.append("quality gain below promotion floor")

        native_detail = native.technical.small_detail_survival
        challenger_detail = challenger.technical.small_detail_survival
        detail_delta = (
            challenger_detail - native_detail
            if native_detail is not None and challenger_detail is not None
            else None
        )
        if detail_delta is None:
            reasons.append("small-detail survival evidence unavailable")
        elif detail_delta < spec.min_detail_gain:
            reasons.append("small-detail gain below promotion floor")

        semantic_delta = (
            challenger.semantic_score - native.semantic_score
            if (
                challenger.semantic_score is not None
                and native.semantic_score is not None
            )
            else None
        )
        if semantic_delta is None:
            reasons.append("semantic comparison unavailable")
        elif semantic_delta < -spec.max_semantic_drop:
            reasons.append("semantic regression exceeds safety floor")

        if (
            spec.max_latency_ratio is not None
            and native.operational.latency_ms > 0
            and (
                challenger.operational.latency_ms
                / native.operational.latency_ms
            )
            > spec.max_latency_ratio
        ):
            reasons.append("latency ratio exceeds benchmark ceiling")

        if (
            spec.max_cost_per_case_usd is not None
            and challenger.operational.cost_usd > spec.max_cost_per_case_usd
        ):
            reasons.append("cost per case exceeds benchmark ceiling")

        if SRBenchmarkMatrixRunner._hallucination_risk(
            native,
            challenger,
            max_semantic_drop=spec.max_semantic_drop,
        ):
            reasons.append("semantic hallucination-risk signal detected")

        return (
            not reasons,
            quality_delta,
            detail_delta,
            semantic_delta,
            reasons,
        )

    def run(
        self,
        spec: SRBenchmarkSpec,
        *,
        spec_base: Path | None = None,
    ) -> SRBenchmarkReport:
        recipe_path = self._resolve(spec.recipe_path, spec_base)
        assert recipe_path is not None
        base_recipe = load_recipe(recipe_path)

        manifest_paths = {
            SRBenchmarkLane.NATIVE: self._resolve(
                spec.native_manifest_path,
                spec_base,
            ),
            SRBenchmarkLane.LANCZOS: self._resolve(
                spec.lanczos_manifest_path,
                spec_base,
            ),
            SRBenchmarkLane.LOCAL_SR: self._resolve(
                spec.local_sr_manifest_path,
                spec_base,
            ),
            SRBenchmarkLane.REMOTE_SR: self._resolve(
                spec.remote_sr_manifest_path,
                spec_base,
            ),
        }

        scorecards: dict[SRBenchmarkLane, BenchmarkScorecard] = {}
        results_by_lane: dict[
            SRBenchmarkLane,
            dict[str, BenchmarkCaseResult],
        ] = {}
        runs: list[SRLaneRun] = []

        native_recipe = self._recipe_for_lane(
            base_recipe,
            SRBenchmarkLane.NATIVE,
        )
        native_manifest = manifest_paths[SRBenchmarkLane.NATIVE]
        if native_manifest is None:
            native_scorecard = HarnessEngineRunner(
                self.settings,
                self.registry,
                self.store,
            ).run(
                spec.dataset_id,
                spec.tier,
                native_recipe,
                quality_mode=spec.quality_mode,
                limit=spec.limit,
                recipe_base=recipe_path.parent,
            )
            native_results = self.store.get_results(native_scorecard.run_id)
        else:
            native_scorecard, native_results = self._run_manifest_lane(
                lane=SRBenchmarkLane.NATIVE,
                spec=spec,
                recipe=native_recipe,
                manifest_path=native_manifest,
            )
        scorecards[SRBenchmarkLane.NATIVE] = native_scorecard
        results_by_lane[SRBenchmarkLane.NATIVE] = {
            result.pair_id: result for result in native_results
        }
        runs.append(
            self._lane_run(
                SRBenchmarkLane.NATIVE,
                native_scorecard,
                native_results,
            )
        )

        for lane in (
            SRBenchmarkLane.LANCZOS,
            SRBenchmarkLane.LOCAL_SR,
            SRBenchmarkLane.REMOTE_SR,
        ):
            manifest_path = manifest_paths[lane]
            if manifest_path is None:
                results_by_lane[lane] = {}
                if lane is SRBenchmarkLane.LANCZOS:
                    continue
                runs.append(
                    self._unavailable_run(
                        lane,
                        "no candidate manifest/backend configured",
                    )
                )
                continue

            try:
                lane_recipe = self._recipe_for_lane(base_recipe, lane)
                scorecard, results = self._run_manifest_lane(
                    lane=lane,
                    spec=spec,
                    recipe=lane_recipe,
                    manifest_path=manifest_path,
                )
            except (OSError, ValueError) as exc:
                runs.append(
                    self._unavailable_run(
                        lane,
                        f"{type(exc).__name__}: {exc}",
                    )
                )
                results_by_lane[lane] = {}
                continue

            scorecards[lane] = scorecard
            results_by_lane[lane] = {
                result.pair_id: result for result in results
            }
            runs.append(self._lane_run(lane, scorecard, results))

        fingerprint_values = [
            scorecard.provenance.dataset_manifest_sha256
            for scorecard in scorecards.values()
        ]
        if (
            not fingerprint_values
            or any(not value for value in fingerprint_values)
            or len(set(fingerprint_values)) != 1
        ):
            raise ValueError(
                "SR benchmark matrix requires one shared dataset manifest fingerprint"
            )
        dataset_fingerprint = fingerprint_values[0]

        native_map = results_by_lane[SRBenchmarkLane.NATIVE]
        pair_ids = sorted(
            set(native_map)
            | set(results_by_lane[SRBenchmarkLane.LANCZOS])
            | set(results_by_lane[SRBenchmarkLane.LOCAL_SR])
            | set(results_by_lane[SRBenchmarkLane.REMOTE_SR])
        )

        comparisons: list[SRCaseComparison] = []
        for pair_id in pair_ids:
            native = native_map.get(pair_id)
            lanczos = results_by_lane[SRBenchmarkLane.LANCZOS].get(pair_id)
            local = results_by_lane[SRBenchmarkLane.LOCAL_SR].get(pair_id)
            remote = results_by_lane[SRBenchmarkLane.REMOTE_SR].get(pair_id)
            all_results = {
                SRBenchmarkLane.NATIVE: native,
                SRBenchmarkLane.LANCZOS: lanczos,
                SRBenchmarkLane.LOCAL_SR: local,
                SRBenchmarkLane.REMOTE_SR: remote,
            }
            lane_evidence = [
                self._base_evidence(lane, result)
                for lane, result in all_results.items()
                if result is not None
            ]

            if (
                native is None
                or not native.success
                or native.quality_score is None
            ):
                comparisons.append(
                    SRCaseComparison(
                        pair_id=pair_id,
                        artwork_identity=(
                            native.artwork_identity
                            if native is not None
                            else (
                                local.artwork_identity
                                if local is not None
                                else (
                                    remote.artwork_identity
                                    if remote is not None
                                    else pair_id
                                )
                            )
                        ),
                        lanes=lane_evidence,
                        preferred_lane=SRCasePreference.UNUSABLE,
                        reasons=["native baseline unavailable"],
                    )
                )
                continue

            baseline_lane = SRBenchmarkLane.NATIVE
            baseline = native
            if (
                lanczos is not None
                and lanczos.success
                and lanczos.quality_score is not None
                and self._same_pre_sr_source(native, lanczos)
            ):
                baseline_lane = SRBenchmarkLane.LANCZOS
                baseline = lanczos

            eligible: list[
                tuple[
                    SRBenchmarkLane,
                    BenchmarkCaseResult,
                    float,
                    float,
                    float,
                ]
            ] = []
            comparison_reasons: list[str] = []
            for lane, challenger in (
                (SRBenchmarkLane.LOCAL_SR, local),
                (SRBenchmarkLane.REMOTE_SR, remote),
            ):
                if challenger is None:
                    continue
                (
                    qualifies,
                    quality_delta,
                    detail_delta,
                    semantic_delta,
                    reasons,
                ) = self._challenger_qualifies(baseline, challenger, spec)

                evidence = next(
                    item for item in lane_evidence if item.lane is lane
                )
                evidence.hallucination_risk = self._hallucination_risk(
                    baseline,
                    challenger,
                    max_semantic_drop=spec.max_semantic_drop,
                )
                for reason in reasons:
                    if reason not in evidence.reasons:
                        evidence.reasons.append(reason)

                if qualifies:
                    eligible.append(
                        (
                            lane,
                            challenger,
                            quality_delta or 0.0,
                            detail_delta or 0.0,
                            semantic_delta or 0.0,
                        )
                    )
                elif reasons:
                    comparison_reasons.append(
                        f"{lane.value}: " + ", ".join(reasons)
                    )

            if not eligible:
                comparisons.append(
                    SRCaseComparison(
                        pair_id=pair_id,
                        artwork_identity=native.artwork_identity,
                        lanes=lane_evidence,
                        preferred_lane=SRCasePreference(
                            baseline_lane.value
                        ),
                        quality_gain=0.0,
                        detail_gain=0.0,
                        semantic_delta=0.0,
                        reasons=(
                            comparison_reasons
                            or [
                                "no SR challenger has promotable evidence; "
                                f"keeping {baseline_lane.value} baseline"
                            ]
                        ),
                    )
                )
                continue

            eligible.sort(
                key=lambda item: (
                    item[2],
                    item[3],
                    item[4],
                    -item[1].operational.latency_ms,
                    -item[1].operational.cost_usd,
                ),
                reverse=True,
            )
            winner = eligible[0]
            tied = (
                len(eligible) > 1
                and abs(eligible[0][2] - eligible[1][2]) < 0.005
                and abs(eligible[0][3] - eligible[1][3]) < 0.01
            )
            preferred = (
                SRCasePreference.TIE
                if tied
                else SRCasePreference(winner[0].value)
            )
            comparisons.append(
                SRCaseComparison(
                    pair_id=pair_id,
                    artwork_identity=native.artwork_identity,
                    lanes=lane_evidence,
                    preferred_lane=preferred,
                    quality_gain=winner[2],
                    detail_gain=winner[3],
                    semantic_delta=winner[4],
                    reasons=(
                        ["local/remote SR challengers are effectively tied"]
                        if tied
                        else [
                            f"{winner[0].value} meets benchmark promotion floors"
                        ]
                    ),
                )
            )

        comparable = [
            item
            for item in comparisons
            if item.preferred_lane is not SRCasePreference.UNUSABLE
        ]
        native_count = sum(
            item.preferred_lane is SRCasePreference.NATIVE
            for item in comparable
        )
        lanczos_count = sum(
            item.preferred_lane is SRCasePreference.LANCZOS
            for item in comparable
        )
        local_count = sum(
            item.preferred_lane is SRCasePreference.LOCAL_SR
            for item in comparable
        )
        remote_count = sum(
            item.preferred_lane is SRCasePreference.REMOTE_SR
            for item in comparable
        )
        tie_count = sum(
            item.preferred_lane is SRCasePreference.TIE
            for item in comparable
        )

        reasons: list[str] = [
            "benchmark evidence only; production SR execution remains disabled"
        ]
        unavailable = [run for run in runs if not run.available]
        for run in unavailable:
            reasons.append(
                f"{run.lane.value} unavailable: {run.unavailable_reason}"
            )
        if any(
            run.available
            and run.status is not HarnessRunStatus.COMPLETE
            for run in runs
        ):
            reasons.append("one or more available SR benchmark runs are incomplete")

        available_challengers = [
            run
            for run in runs
            if (
                run.lane
                in {SRBenchmarkLane.LOCAL_SR, SRBenchmarkLane.REMOTE_SR}
                and run.available
            )
        ]
        if not comparable or not available_challengers:
            recommendation = SRBenchmarkRecommendation.INSUFFICIENT_EVIDENCE
        elif local_count and remote_count:
            recommendation = (
                SRBenchmarkRecommendation.MIXED_POLICY_FOR_HUMAN_REVIEW
            )
        elif local_count:
            recommendation = (
                SRBenchmarkRecommendation.LOCAL_SR_FOR_HUMAN_REVIEW
            )
        elif remote_count:
            recommendation = (
                SRBenchmarkRecommendation.REMOTE_SR_FOR_HUMAN_REVIEW
            )
        elif lanczos_count:
            recommendation = SRBenchmarkRecommendation.KEEP_LANCZOS
        else:
            recommendation = SRBenchmarkRecommendation.KEEP_NATIVE

        report = SRBenchmarkReport(
            matrix_id=spec.matrix_id,
            dataset_id=spec.dataset_id,
            tier=spec.tier,
            recipe_id=base_recipe.recipe_id,
            recipe_version=base_recipe.version,
            quality_mode=spec.quality_mode,
            min_quality_gain=spec.min_quality_gain,
            min_detail_gain=spec.min_detail_gain,
            max_semantic_drop=spec.max_semantic_drop,
            dataset_manifest_sha256=dataset_fingerprint,
            runs=runs,
            comparisons=comparisons,
            comparable_case_count=len(comparable),
            native_preferred_count=native_count,
            lanczos_preferred_count=lanczos_count,
            local_sr_preferred_count=local_count,
            remote_sr_preferred_count=remote_count,
            tie_count=tie_count,
            incomplete_count=sum(
                item.preferred_lane is SRCasePreference.UNUSABLE
                for item in comparisons
            ),
            recommendation=recommendation,
            requires_human_approval=True,
            auto_applied=False,
            production_execution_enabled=False,
            reasons=reasons,
        )

        output_dir = self.store.sr_matrix_dir(spec.matrix_id)
        self.store.save_model(output_dir / "spec.json", spec)
        self.store.save_model(output_dir / "report.json", report)
        return report


def load_sr_benchmark_report(path: Path) -> SRBenchmarkReport:
    return SRBenchmarkReport.model_validate_json(
        path.read_text(encoding="utf-8")
    )
