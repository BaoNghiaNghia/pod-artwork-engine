from __future__ import annotations

from pathlib import Path

from .harness import HarnessStore
from .harness_models import (
    BenchmarkTier,
    HarnessRunStatus,
    SRAdapterKind,
    SRAdapterRunReport,
    SRBenchmarkLane,
    SRBenchmarkRecommendation,
    SRBenchmarkReport,
    SRBenchmarkSpec,
    SRCohortReport,
    SRCohortSpec,
    SRExperimentReport,
    SRExperimentSpec,
    SRPolicyProposal,
    SRPolicyRecommendation,
)
from .settings import Settings
from .sr_adapters import SRAdapterMaterializer, load_sr_adapter_spec
from .sr_cohort import SRCohortMaterializer
from .sr_matrix import SRBenchmarkMatrixRunner


def build_sr_policy_proposal(
    spec: SRExperimentSpec,
    cohort: SRCohortReport,
    matrix: SRBenchmarkReport,
    *,
    local_adapter: SRAdapterRunReport | None = None,
    remote_adapter: SRAdapterRunReport | None = None,
) -> SRPolicyProposal:
    reasons: list[str] = [
        "evidence-only SR policy proposal; production execution remains disabled"
    ]

    if cohort.dataset_manifest_sha256 != matrix.dataset_manifest_sha256:
        raise ValueError(
            "SR experiment requires matching cohort and matrix dataset fingerprints"
        )
    if cohort.dataset_id != matrix.dataset_id or cohort.dataset_id != spec.dataset_id:
        raise ValueError("SR experiment dataset identity mismatch")
    if cohort.tier is not matrix.tier or cohort.tier is not spec.tier:
        raise ValueError("SR experiment benchmark tier mismatch")

    local_available = any(
        run.lane is SRBenchmarkLane.LOCAL_SR and run.available
        for run in matrix.runs
    )
    remote_available = any(
        run.lane is SRBenchmarkLane.REMOTE_SR and run.available
        for run in matrix.runs
    )

    case_total = matrix.comparable_case_count + matrix.incomplete_count
    incomplete_rate = (
        matrix.incomplete_count / case_total
        if case_total
        else 1.0
    )

    blocking: list[str] = []
    if spec.require_golden and matrix.tier is not BenchmarkTier.GOLDEN:
        blocking.append("Golden Holdout evidence is required")
    if not matrix.dataset_manifest_sha256:
        blocking.append("dataset fingerprint is unavailable")
    if not cohort.shared_input_identity or cohort.failure_count:
        blocking.append("pre-SR cohort is incomplete or not source-identical")
    if matrix.comparable_case_count < spec.min_comparable_cases:
        blocking.append(
            "comparable Golden cases below minimum evidence threshold"
        )
    if incomplete_rate > spec.max_incomplete_rate:
        blocking.append("incomplete-case rate exceeds evidence ceiling")
    if not (local_available or remote_available):
        blocking.append("no measured SR backend is available")
    measured_sr_runs = [
        run
        for run in matrix.runs
        if run.lane in {SRBenchmarkLane.LOCAL_SR, SRBenchmarkLane.REMOTE_SR}
        and run.available
    ]
    if any(
        run.status is not HarnessRunStatus.COMPLETE
        or run.success_count < matrix.comparable_case_count
        for run in measured_sr_runs
    ):
        blocking.append("one or more measured SR backend runs are incomplete")
    if matrix.recommendation is SRBenchmarkRecommendation.INSUFFICIENT_EVIDENCE:
        blocking.append("SR benchmark matrix reports insufficient evidence")

    recommendation = SRPolicyRecommendation.MANUAL_REVIEW
    decisive_wins = (
        matrix.local_sr_preferred_count + matrix.remote_sr_preferred_count
    )

    if not blocking:
        if matrix.recommendation is SRBenchmarkRecommendation.KEEP_NATIVE:
            recommendation = SRPolicyRecommendation.KEEP_NATIVE
        elif matrix.recommendation is SRBenchmarkRecommendation.KEEP_LANCZOS:
            recommendation = SRPolicyRecommendation.KEEP_LANCZOS
        elif (
            matrix.recommendation
            is SRBenchmarkRecommendation.LOCAL_SR_FOR_HUMAN_REVIEW
        ):
            if not local_available:
                blocking.append("local SR recommendation has no available backend")
            elif decisive_wins < spec.min_decisive_wins:
                blocking.append("local SR wins below promotion evidence threshold")
            else:
                recommendation = SRPolicyRecommendation.LOCAL_SR
        elif (
            matrix.recommendation
            is SRBenchmarkRecommendation.REMOTE_SR_FOR_HUMAN_REVIEW
        ):
            if not remote_available:
                blocking.append("remote SR recommendation has no available backend")
            elif decisive_wins < spec.min_decisive_wins:
                blocking.append("remote SR wins below promotion evidence threshold")
            else:
                recommendation = SRPolicyRecommendation.REMOTE_SR
        elif (
            matrix.recommendation
            is SRBenchmarkRecommendation.MIXED_POLICY_FOR_HUMAN_REVIEW
        ):
            if not (local_available and remote_available):
                blocking.append(
                    "mixed SR recommendation requires both measured backends"
                )
            elif (
                matrix.local_sr_preferred_count < 1
                or matrix.remote_sr_preferred_count < 1
                or decisive_wins < spec.min_decisive_wins
            ):
                blocking.append("mixed SR wins below promotion evidence threshold")
            else:
                recommendation = SRPolicyRecommendation.MIXED

    if blocking:
        recommendation = SRPolicyRecommendation.MANUAL_REVIEW
        reasons.extend(blocking)
    else:
        reasons.append(
            f"{recommendation.value} has sufficient benchmark evidence for human review"
        )

    if local_adapter is not None and not local_adapter.backend_available:
        reasons.append("configured local SR backend was unavailable")
    if remote_adapter is not None and not remote_adapter.backend_available:
        reasons.append("configured remote SR backend was unavailable")

    return SRPolicyProposal(
        experiment_id=spec.experiment_id,
        dataset_id=spec.dataset_id,
        tier=spec.tier,
        dataset_manifest_sha256=matrix.dataset_manifest_sha256,
        cohort_id=cohort.cohort_id,
        cohort_case_count=cohort.case_count,
        cohort_success_count=cohort.success_count,
        comparable_case_count=matrix.comparable_case_count,
        incomplete_count=matrix.incomplete_count,
        native_preferred_count=matrix.native_preferred_count,
        lanczos_preferred_count=matrix.lanczos_preferred_count,
        local_sr_preferred_count=matrix.local_sr_preferred_count,
        remote_sr_preferred_count=matrix.remote_sr_preferred_count,
        tie_count=matrix.tie_count,
        local_backend_available=local_available,
        remote_backend_available=remote_available,
        min_quality_gain=spec.min_quality_gain,
        min_detail_gain=spec.min_detail_gain,
        max_semantic_drop=spec.max_semantic_drop,
        max_latency_ratio=spec.max_latency_ratio,
        max_cost_per_case_usd=spec.max_cost_per_case_usd,
        min_comparable_cases=spec.min_comparable_cases,
        min_decisive_wins=spec.min_decisive_wins,
        max_incomplete_rate=spec.max_incomplete_rate,
        recommendation=recommendation,
        sufficient_evidence=not blocking,
        requires_human_approval=True,
        automatically_applied=False,
        production_execution_enabled=False,
        reasons=list(dict.fromkeys(reasons)),
    )


class SRExperimentRunner:
    """Run the benchmark-only Golden SR experiment from one pre-SR cohort."""

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
    def _validate_adapter(
        path: Path,
        *,
        expected_kind: SRAdapterKind,
        scale_factor: float,
    ):
        adapter = load_sr_adapter_spec(path)
        if adapter.kind is not expected_kind:
            raise ValueError(
                f"{path} must define {expected_kind.value}, got {adapter.kind.value}"
            )
        if abs(adapter.scale_factor - scale_factor) > 0.01:
            raise ValueError(
                "SR adapter scale_factor must match fair cohort scale_factor"
            )
        return adapter

    def run(
        self,
        spec: SRExperimentSpec,
        *,
        spec_base: Path | None = None,
    ) -> SRExperimentReport:
        if spec.require_golden and spec.tier is not BenchmarkTier.GOLDEN:
            raise ValueError(
                "SR experiment requires Golden Holdout by default; "
                "set require_golden=false only for pre-Golden diagnostics"
            )

        pre_sr_manifest = self._resolve(spec.pre_sr_manifest_path, spec_base)
        recipe_path = self._resolve(spec.recipe_path, spec_base)
        local_adapter_path = self._resolve(spec.local_adapter_path, spec_base)
        remote_adapter_path = self._resolve(spec.remote_adapter_path, spec_base)
        assert pre_sr_manifest is not None
        assert recipe_path is not None

        cohort_spec = SRCohortSpec(
            cohort_id=f"{spec.experiment_id}_cohort",
            dataset_id=spec.dataset_id,
            tier=spec.tier,
            input_manifest_path=str(pre_sr_manifest),
            scale_factor=spec.scale_factor,
            max_output_megapixels=spec.max_output_megapixels,
            limit=spec.limit,
        )
        cohort = SRCohortMaterializer(
            self.settings,
            self.registry,
            self.store,
        ).materialize(cohort_spec)

        local_report: SRAdapterRunReport | None = None
        remote_report: SRAdapterRunReport | None = None
        materializer = SRAdapterMaterializer(
            self.settings,
            self.registry,
            self.store,
        )

        if local_adapter_path is not None:
            local_spec = self._validate_adapter(
                local_adapter_path,
                expected_kind=SRAdapterKind.LOCAL_COMMAND,
                scale_factor=spec.scale_factor,
            )
            local_report = materializer.materialize(
                dataset_id=spec.dataset_id,
                tier=spec.tier,
                source_manifest_path=Path(cohort.source_manifest_path),
                adapter_spec=local_spec,
                quality_mode=spec.quality_mode,
                limit=spec.limit,
            )

        if remote_adapter_path is not None:
            remote_spec = self._validate_adapter(
                remote_adapter_path,
                expected_kind=SRAdapterKind.REMOTE_PROVIDER,
                scale_factor=spec.scale_factor,
            )
            remote_report = materializer.materialize(
                dataset_id=spec.dataset_id,
                tier=spec.tier,
                source_manifest_path=Path(cohort.source_manifest_path),
                adapter_spec=remote_spec,
                quality_mode=spec.quality_mode,
                limit=spec.limit,
            )

        matrix_spec = SRBenchmarkSpec(
            matrix_id=f"{spec.experiment_id}_matrix",
            dataset_id=spec.dataset_id,
            tier=spec.tier,
            recipe_path=str(recipe_path),
            native_manifest_path=cohort.native_manifest_path,
            lanczos_manifest_path=cohort.lanczos_manifest_path,
            local_sr_manifest_path=(
                local_report.output_manifest_path
                if local_report is not None and local_report.backend_available
                else None
            ),
            remote_sr_manifest_path=(
                remote_report.output_manifest_path
                if remote_report is not None and remote_report.backend_available
                else None
            ),
            quality_mode=spec.quality_mode,
            limit=spec.limit,
            min_quality_gain=spec.min_quality_gain,
            min_detail_gain=spec.min_detail_gain,
            max_semantic_drop=spec.max_semantic_drop,
            max_latency_ratio=spec.max_latency_ratio,
            max_cost_per_case_usd=spec.max_cost_per_case_usd,
        )
        matrix = SRBenchmarkMatrixRunner(
            self.settings,
            self.registry,
            self.store,
        ).run(matrix_spec)

        proposal = build_sr_policy_proposal(
            spec,
            cohort,
            matrix,
            local_adapter=local_report,
            remote_adapter=remote_report,
        )

        experiment_dir = self.store.sr_experiment_dir(spec.experiment_id)
        cohort_report_path = (
            self.store.sr_cohort_dir(cohort.cohort_id) / "report.json"
        )
        matrix_report_path = (
            self.store.sr_matrix_dir(matrix.matrix_id) / "report.json"
        )
        local_report_path = (
            self.store.sr_adapter_dir(local_report.run_id) / "report.json"
            if local_report is not None
            else None
        )
        remote_report_path = (
            self.store.sr_adapter_dir(remote_report.run_id) / "report.json"
            if remote_report is not None
            else None
        )
        policy_path = experiment_dir / "policy-proposal.json"

        report = SRExperimentReport(
            experiment_id=spec.experiment_id,
            dataset_id=spec.dataset_id,
            tier=spec.tier,
            cohort_id=cohort.cohort_id,
            matrix_id=matrix.matrix_id,
            cohort_report_path=str(cohort_report_path),
            local_adapter_report_path=(
                str(local_report_path) if local_report_path is not None else None
            ),
            remote_adapter_report_path=(
                str(remote_report_path) if remote_report_path is not None else None
            ),
            matrix_report_path=str(matrix_report_path),
            policy_proposal_path=str(policy_path),
            policy=proposal,
            reasons=[
                "Golden SR experiment is benchmark-only",
                "policy proposal requires explicit human approval",
                "production SR execution remains disabled",
            ],
        )
        self.store.save_model(experiment_dir / "spec.json", spec)
        self.store.save_model(policy_path, proposal)
        self.store.save_model(experiment_dir / "report.json", report)
        return report
