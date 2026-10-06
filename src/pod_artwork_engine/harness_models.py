from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import Field, model_validator

from .contracts import ArtworkType, QualityMode, RouteKind, SCHEMA_VERSION, SemanticJudgeResult, StrictModel, utc_now
from .qc_policy import QCModePolicy, QCPolicy
from .router_policy import RouterPolicy


class BenchmarkTier(StrEnum):
    SMOKE = "smoke"
    REGRESSION = "regression"
    GOLDEN = "golden"


class HarnessRunStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class RecipeStage(StrictModel):
    name: str
    implementation: str
    version: str = "1"
    enabled: bool = True
    parameters: dict[str, Any] = Field(default_factory=dict)


class BenchmarkRecipe(StrictModel):
    schema_version: str = SCHEMA_VERSION
    recipe_id: str
    version: str
    description: str = ""
    stages: list[RecipeStage] = Field(default_factory=list)
    router_version: str = ""
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    provider_mapping: dict[str, str] = Field(default_factory=dict)
    qc_policy_version: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunProvenance(StrictModel):
    engine_version: str = ""
    execution_kind: str = "candidate_manifest"
    quality_mode: QualityMode | None = None
    recipe_sha256: str = ""
    dataset_manifest_sha256: str = ""
    qc_policy_id: str = ""
    qc_policy_version: str = ""
    router_policy_id: str = ""
    router_policy_version: str = ""
    route_override: RouteKind | None = None
    provider_recipe_id: str = ""


class BenchmarkPlan(StrictModel):
    schema_version: str = SCHEMA_VERSION
    plan_id: str = Field(default_factory=lambda: "plan_" + uuid4().hex)
    run_id: str
    dataset_id: str
    tier: BenchmarkTier
    recipe_id: str
    recipe_version: str
    case_ids: list[str] = Field(min_length=1)
    provenance: RunProvenance = Field(default_factory=RunProvenance)
    created_at: datetime = Field(default_factory=utc_now)


class BenchmarkCase(StrictModel):
    schema_version: str = SCHEMA_VERSION
    case_id: str
    dataset_id: str
    pair_id: str
    artwork_identity: str
    source_paths: list[str] = Field(min_length=1)
    target_path: str
    exact_text: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    difficulty: str = "unknown"
    cohorts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperationalMetrics(StrictModel):
    latency_ms: float = Field(default=0, ge=0)
    gpu_time_ms: float = Field(default=0, ge=0)
    peak_vram_mb: float = Field(default=0, ge=0)
    peak_ram_mb: float = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    provider_calls: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    manual_review: bool = False


class PrecisionEvidence(StrictModel):
    multi_reference_fusion: bool = False
    reference_count: int = Field(default=0, ge=0)
    consistent_reference_count: int = Field(default=0, ge=0)
    conflicting_reference_count: int = Field(default=0, ge=0)
    reference_consensus_confidence: float = Field(default=0, ge=0, le=1)
    reference_alignment: bool = False
    reference_alignment_aligned_count: int = Field(default=0, ge=0)
    reference_alignment_affine_count: int = Field(default=0, ge=0)
    reference_alignment_homography_count: int = Field(default=0, ge=0)
    reference_alignment_semantic_count: int = Field(default=0, ge=0)
    reference_alignment_manual_count: int = Field(default=0, ge=0)
    reference_alignment_mean_confidence: float = Field(default=0, ge=0, le=1)
    reference_alignment_fail_closed: bool = False
    feature_correspondence: bool = False
    feature_correspondence_measured_affine_count: int = Field(default=0, ge=0)
    feature_correspondence_measured_homography_count: int = Field(default=0, ge=0)
    feature_correspondence_insufficient_count: int = Field(default=0, ge=0)
    feature_correspondence_semantic_count: int = Field(default=0, ge=0)
    feature_correspondence_manual_count: int = Field(default=0, ge=0)
    feature_correspondence_mean_inlier_ratio: float = Field(default=0, ge=0, le=1)
    feature_correspondence_mean_reprojection_error: float | None = Field(
        default=None,
        ge=0,
    )
    feature_correspondence_fail_closed: bool = False
    region_confidence_map: bool = False
    region_grid_cells: int = Field(default=0, ge=0)
    region_mean_confidence: float = Field(default=0, ge=0, le=1)
    region_support_coverage: float = Field(default=0, ge=0, le=1)
    low_confidence_region_count: int = Field(default=0, ge=0)
    region_rescue_plan: bool = False
    region_rescue_disposition: str = ""
    region_rescue_target_count: int = Field(default=0, ge=0)
    region_rescue_target_cell_count: int = Field(default=0, ge=0)
    region_rescue_fail_closed: bool = False
    representation_plan: bool = False
    representation_overall: str = ""
    representation_vector_components: int = Field(default=0, ge=0)
    representation_raster_components: int = Field(default=0, ge=0)
    representation_fail_closed: bool = False
    material_separation: bool = False
    material_separation_disposition: str = ""
    material_separation_confidence: float = Field(default=0, ge=0, le=1)
    material_separation_fail_closed: bool = False
    material_border_uniformity: float = Field(default=0, ge=0, le=1)
    material_edge_contact_ratio: float = Field(default=0, ge=0, le=1)
    texture_handling: bool = False
    texture_handling_disposition: str = ""
    texture_handling_confidence: float = Field(default=0, ge=0, le=1)
    texture_handling_fail_closed: bool = False
    texture_edge_density: float = Field(default=0, ge=0, le=1)
    texture_local_contrast: float = Field(default=0, ge=0, le=1)
    texture_local_variation: float = Field(default=0, ge=0, le=1)
    texture_native_long_edge: int = Field(default=0, ge=0)
    super_resolution_readiness: bool = False
    super_resolution_disposition: str = ""
    super_resolution_confidence: float = Field(default=0, ge=0, le=1)
    super_resolution_fail_closed: bool = False
    super_resolution_scale_factor: float = Field(default=0, ge=0)
    super_resolution_native_long_edge: int = Field(default=0, ge=0)
    super_resolution_target_long_edge: int = Field(default=0, ge=0)
    super_resolution_provider_available: bool = False
    local_ocr: bool = False
    ocr_backend: str = ""
    ocr_backend_source: str = ""
    ocr_backend_version: str = ""
    visual_font_match: bool = False
    matched_font_lines: int = Field(default=0, ge=0)
    typography_rebuilt: bool = False
    mixed_text_refined: bool = False
    local_text_repair: bool = False
    repaired_text_regions: int = Field(default=0, ge=0)
    geometry_vector: bool = False
    compound_geometry: bool = False
    geometry_subpaths: int = Field(default=0, ge=0)
    evenodd_compound_fills: int = Field(default=0, ge=0)
    masked_text_regions: int = Field(default=0, ge=0)
    provider_recipe_id: str = ""
    precision_ops: list[str] = Field(default_factory=list)


class RouteEvidence(StrictModel):
    selected_route: RouteKind | None = None
    requested_override: RouteKind | None = None
    used_remote: bool = False
    remote_available: bool = False
    design_confidence: float | None = Field(default=None, ge=0, le=1)
    artwork_type: ArtworkType | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class RuntimeQCEvidence(StrictModel):
    semantic_score: float | None = Field(default=None, ge=0, le=1)
    technical_score: float | None = Field(default=None, ge=0, le=1)
    semantic_passed: bool | None = None
    technical_passed: bool | None = None
    object_fidelity: float | None = Field(default=None, ge=0, le=1)
    resolution_score: float | None = Field(default=None, ge=0, le=1)
    analysis_confidence: float | None = Field(default=None, ge=0, le=1)


class SemanticMetrics(StrictModel):
    exact_text: float | None = Field(default=None, ge=0, le=1)
    layout: float | None = Field(default=None, ge=0, le=1)
    object_fidelity: float | None = Field(default=None, ge=0, le=1)
    color: float | None = Field(default=None, ge=0, le=1)
    texture: float | None = Field(default=None, ge=0, le=1)
    missing_detail: float | None = Field(default=None, ge=0, le=1)


class TechnicalMetrics(StrictModel):
    edge: float | None = Field(default=None, ge=0, le=1)
    alpha: float | None = Field(default=None, ge=0, le=1)
    halo_aliasing: float | None = Field(default=None, ge=0, le=1)
    blur: float | None = Field(default=None, ge=0, le=1)
    effective_resolution: float | None = Field(default=None, ge=0, le=1)
    small_detail_survival: float | None = Field(default=None, ge=0, le=1)


class BenchmarkCaseResult(StrictModel):
    schema_version: str = SCHEMA_VERSION
    case_id: str
    pair_id: str
    artwork_identity: str
    candidate_path: str | None = None
    success: bool
    error: str | None = None
    semantic: SemanticMetrics = Field(default_factory=SemanticMetrics)
    technical: TechnicalMetrics = Field(default_factory=TechnicalMetrics)
    operational: OperationalMetrics = Field(default_factory=OperationalMetrics)
    precision: PrecisionEvidence = Field(default_factory=PrecisionEvidence)
    route: RouteEvidence = Field(default_factory=RouteEvidence)
    runtime_qc: RuntimeQCEvidence = Field(default_factory=RuntimeQCEvidence)
    semantic_score: float | None = Field(default=None, ge=0, le=1)
    technical_score: float | None = Field(default=None, ge=0, le=1)
    quality_score: float | None = Field(default=None, ge=0, le=1)
    diff_path: str | None = None
    cohorts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CohortScore(StrictModel):
    cohort: str
    case_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    quality_mean: float | None = Field(default=None, ge=0, le=1)
    semantic_mean: float | None = Field(default=None, ge=0, le=1)
    technical_mean: float | None = Field(default=None, ge=0, le=1)
    failure_rate: float = Field(default=0, ge=0, le=1)


class BenchmarkScorecard(StrictModel):
    schema_version: str = SCHEMA_VERSION
    scorecard_id: str = Field(default_factory=lambda: "score_" + uuid4().hex)
    run_id: str
    dataset_id: str
    tier: BenchmarkTier
    recipe_id: str
    recipe_version: str
    status: HarnessRunStatus
    case_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    manual_review_count: int = Field(ge=0)
    quality_mean: float | None = Field(default=None, ge=0, le=1)
    semantic_mean: float | None = Field(default=None, ge=0, le=1)
    technical_mean: float | None = Field(default=None, ge=0, le=1)
    failure_rate: float = Field(default=0, ge=0, le=1)
    manual_review_rate: float = Field(default=0, ge=0, le=1)
    latency_p50_ms: float = Field(default=0, ge=0)
    latency_p95_ms: float = Field(default=0, ge=0)
    provider_calls: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    total_cost_usd: float = Field(default=0, ge=0)
    metric_coverage: dict[str, float] = Field(default_factory=dict)
    precision_coverage: dict[str, float] = Field(default_factory=dict)
    provenance: RunProvenance = Field(default_factory=RunProvenance)
    cohorts: dict[str, CohortScore] = Field(default_factory=dict)
    result_paths: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class PromotionPolicy(StrictModel):
    min_overall_delta: float = 0
    require_complete: bool = True
    max_cohort_drop: float = Field(default=0.03, ge=0, le=1)
    max_failure_rate_increase: float = Field(default=0, ge=0, le=1)
    max_manual_review_rate_increase: float = Field(default=0, ge=0, le=1)
    max_latency_ratio: float | None = Field(default=None, ge=0)
    max_cost_ratio: float | None = Field(default=None, ge=0)
    require_golden: bool = True


class PromotionDecision(StrictModel):
    schema_version: str = SCHEMA_VERSION
    champion_scorecard_id: str
    challenger_scorecard_id: str
    eligible: bool
    reasons: list[str] = Field(default_factory=list)
    overall_delta: float | None = None
    cohort_deltas: dict[str, float] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class SuiteRecommendation(StrEnum):
    INCOMPLETE = "incomplete"
    REJECTED = "rejected"
    ELIGIBLE_FOR_HUMAN_REVIEW = "eligible_for_human_review"


class BenchmarkSuiteSpec(StrictModel):
    schema_version: str = SCHEMA_VERSION
    suite_id: str = Field(default_factory=lambda: "suite_" + uuid4().hex)
    dataset_id: str
    champion_recipe_path: str
    challenger_recipe_paths: list[str] = Field(min_length=1)
    quality_mode: QualityMode = QualityMode.PRINT_READY
    smoke_limit: int | None = Field(default=8, ge=1)
    regression_limit: int | None = Field(default=None, ge=1)
    golden_limit: int | None = Field(default=None, ge=1)
    promotion_policy: PromotionPolicy = Field(default_factory=PromotionPolicy)
    created_at: datetime = Field(default_factory=utc_now)


class SuiteTierResult(StrictModel):
    tier: BenchmarkTier
    champion_run_id: str
    challenger_run_id: str
    decision: PromotionDecision
    passed_gate: bool
    reasons: list[str] = Field(default_factory=list)


class ChallengerSuiteResult(StrictModel):
    recipe_id: str
    recipe_version: str
    tiers: list[SuiteTierResult] = Field(default_factory=list)
    recommendation: SuiteRecommendation = SuiteRecommendation.INCOMPLETE
    reasons: list[str] = Field(default_factory=list)


class BenchmarkSuiteReport(StrictModel):
    schema_version: str = SCHEMA_VERSION
    suite_id: str
    dataset_id: str
    champion_recipe_id: str
    champion_recipe_version: str
    quality_mode: QualityMode
    challengers: list[ChallengerSuiteResult] = Field(default_factory=list)
    requires_human_approval: bool = True
    auto_promoted: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class ThresholdCalibrationMetric(StrictModel):
    metric: str
    sample_count: int = Field(ge=0)
    good_count: int = Field(ge=0)
    bad_count: int = Field(ge=0)
    current_threshold: float = Field(ge=0, le=1)
    recommended_threshold: float | None = Field(default=None, ge=0, le=1)
    false_accept_rate: float | None = Field(default=None, ge=0, le=1)
    false_reject_rate: float | None = Field(default=None, ge=0, le=1)
    sufficient_evidence: bool = False
    reasons: list[str] = Field(default_factory=list)


class QCPolicyCalibrationProposal(StrictModel):
    schema_version: str = SCHEMA_VERSION
    proposal_id: str = Field(default_factory=lambda: "cal_" + uuid4().hex)
    quality_mode: QualityMode
    source_run_ids: list[str] = Field(min_length=1)
    source_tiers: list[BenchmarkTier] = Field(default_factory=list)
    current_policy: QCModePolicy
    proposed_policy: QCModePolicy
    candidate_policy: QCPolicy
    metrics: dict[str, ThresholdCalibrationMetric] = Field(default_factory=dict)
    good_quality_threshold: float = Field(default=0.85, ge=0, le=1)
    bad_quality_threshold: float = Field(default=0.70, ge=0, le=1)
    sufficient_evidence: bool = False
    requires_human_approval: bool = True
    automatically_applied: bool = False
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class RoutePreference(StrEnum):
    DETERMINISTIC = "deterministic"
    REMOTE = "remote"
    TIE = "tie"
    UNUSABLE = "unusable"


class RouteMatrixSpec(StrictModel):
    schema_version: str = SCHEMA_VERSION
    matrix_id: str = Field(default_factory=lambda: "route_matrix_" + uuid4().hex)
    dataset_id: str
    tier: BenchmarkTier
    recipe_path: str
    quality_mode: QualityMode = QualityMode.PRINT_READY
    routes: list[RouteKind] = Field(
        default_factory=lambda: [RouteKind.DETERMINISTIC, RouteKind.HYBRID],
        min_length=2,
    )
    limit: int | None = Field(default=None, ge=1)
    min_quality_gain: float = Field(default=0.02, ge=0, le=1)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_routes(self) -> "RouteMatrixSpec":
        unique = list(dict.fromkeys(self.routes))
        if len(unique) != len(self.routes):
            raise ValueError("route matrix routes must be unique")
        if RouteKind.DETERMINISTIC not in unique:
            raise ValueError("route matrix requires deterministic baseline")
        if not any(
            route in {RouteKind.HYBRID, RouteKind.REMOTE_SEMANTIC}
            for route in unique
        ):
            raise ValueError("route matrix requires at least one remote-capable route")
        return self


class RouteMatrixRun(StrictModel):
    route: RouteKind
    run_id: str
    scorecard_id: str
    status: HarnessRunStatus


class RouteCaseComparison(StrictModel):
    pair_id: str
    artwork_identity: str
    design_confidence: float | None = Field(default=None, ge=0, le=1)
    artwork_type: ArtworkType | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    deterministic_quality: float | None = Field(default=None, ge=0, le=1)
    remote_quality: float | None = Field(default=None, ge=0, le=1)
    remote_route: RouteKind | None = None
    quality_delta: float | None = Field(default=None, ge=-1, le=1)
    deterministic_latency_ms: float = Field(default=0, ge=0)
    remote_latency_ms: float = Field(default=0, ge=0)
    remote_used: bool = False
    preference: RoutePreference = RoutePreference.UNUSABLE
    reasons: list[str] = Field(default_factory=list)


class RouteMatrixReport(StrictModel):
    schema_version: str = SCHEMA_VERSION
    matrix_id: str
    dataset_id: str
    tier: BenchmarkTier
    recipe_id: str
    recipe_version: str
    quality_mode: QualityMode
    min_quality_gain: float = Field(ge=0, le=1)
    dataset_manifest_sha256: str = ""
    router_policy_id: str = ""
    router_policy_version: str = ""
    runs: list[RouteMatrixRun] = Field(default_factory=list)
    comparisons: list[RouteCaseComparison] = Field(default_factory=list)
    comparable_case_count: int = Field(default=0, ge=0)
    remote_preferred_count: int = Field(default=0, ge=0)
    deterministic_preferred_count: int = Field(default=0, ge=0)
    tie_count: int = Field(default=0, ge=0)
    incomplete_count: int = Field(default=0, ge=0)
    requires_human_approval: bool = True
    auto_applied: bool = False
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class SRAdapterKind(StrEnum):
    LOCAL_COMMAND = "local_command"
    REMOTE_PROVIDER = "remote_provider"


class SRAdapterSpec(StrictModel):
    schema_version: str = SCHEMA_VERSION
    adapter_id: str
    version: str = "1"
    kind: SRAdapterKind
    model_alias: str = ""
    scale_factor: float = Field(default=2.0, gt=1, le=4)
    min_output_scale: float = Field(default=1.05, ge=1, le=4)
    timeout_seconds: float = Field(default=300, gt=0, le=1800)
    max_output_megapixels: float = Field(default=80, gt=0, le=200)
    estimated_cost_usd: float = Field(default=0, ge=0)
    command: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_backend(self) -> "SRAdapterSpec":
        if self.kind is SRAdapterKind.LOCAL_COMMAND:
            if not self.command:
                raise ValueError("local_command adapter requires command tokens")
            command_text = "\n".join(self.command)
            if "{input}" not in command_text or "{output}" not in command_text:
                raise ValueError(
                    "local_command adapter requires {input} and {output} placeholders"
                )
        elif self.command:
            raise ValueError("remote_provider adapter must not define a local command")
        if self.min_output_scale > self.scale_factor:
            raise ValueError("min_output_scale cannot exceed scale_factor")
        return self


class SRAdapterRunReport(StrictModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str = Field(default_factory=lambda: "sr_adapter_" + uuid4().hex)
    dataset_id: str
    tier: BenchmarkTier
    quality_mode: QualityMode = QualityMode.PRINT_READY
    adapter_id: str
    adapter_version: str
    adapter_kind: SRAdapterKind
    model_alias: str = ""
    source_manifest_path: str
    output_manifest_path: str
    case_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    backend_available: bool = True
    benchmark_only: bool = True
    production_execution_enabled: bool = False
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class SRCohortSpec(StrictModel):
    schema_version: str = SCHEMA_VERSION
    cohort_id: str = Field(default_factory=lambda: "sr_cohort_" + uuid4().hex)
    dataset_id: str
    tier: BenchmarkTier
    input_manifest_path: str
    scale_factor: float = Field(default=2.0, gt=1, le=4)
    max_output_megapixels: float = Field(default=80, gt=0, le=200)
    limit: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utc_now)


class SRCohortCaseEvidence(StrictModel):
    pair_id: str
    case_id: str
    artwork_identity: str
    success: bool = False
    source_path: str = ""
    source_sha256: str = ""
    input_width: int = Field(default=0, ge=0)
    input_height: int = Field(default=0, ge=0)
    lanczos_width: int = Field(default=0, ge=0)
    lanczos_height: int = Field(default=0, ge=0)
    native_path: str | None = None
    lanczos_path: str | None = None
    fail_closed: bool = False
    reasons: list[str] = Field(default_factory=list)


class SRCohortReport(StrictModel):
    schema_version: str = SCHEMA_VERSION
    cohort_id: str
    dataset_id: str
    tier: BenchmarkTier
    input_manifest_path: str
    source_manifest_path: str
    native_manifest_path: str
    lanczos_manifest_path: str
    dataset_manifest_sha256: str = ""
    scale_factor: float = Field(gt=1, le=4)
    case_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    shared_input_identity: bool = True
    benchmark_only: bool = True
    production_execution_enabled: bool = False
    cases: list[SRCohortCaseEvidence] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class SRBenchmarkLane(StrEnum):
    NATIVE = "native"
    LANCZOS = "lanczos"
    LOCAL_SR = "local_sr"
    REMOTE_SR = "remote_sr"


class SRCasePreference(StrEnum):
    NATIVE = "native"
    LANCZOS = "lanczos"
    LOCAL_SR = "local_sr"
    REMOTE_SR = "remote_sr"
    TIE = "tie"
    UNUSABLE = "unusable"


class SRBenchmarkRecommendation(StrEnum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    KEEP_NATIVE = "keep_native"
    KEEP_LANCZOS = "keep_lanczos"
    LOCAL_SR_FOR_HUMAN_REVIEW = "local_sr_for_human_review"
    REMOTE_SR_FOR_HUMAN_REVIEW = "remote_sr_for_human_review"
    MIXED_POLICY_FOR_HUMAN_REVIEW = "mixed_policy_for_human_review"


class SRBenchmarkSpec(StrictModel):
    schema_version: str = SCHEMA_VERSION
    matrix_id: str = Field(default_factory=lambda: "sr_matrix_" + uuid4().hex)
    dataset_id: str
    tier: BenchmarkTier
    recipe_path: str
    native_manifest_path: str | None = None
    lanczos_manifest_path: str | None = None
    local_sr_manifest_path: str | None = None
    remote_sr_manifest_path: str | None = None
    quality_mode: QualityMode = QualityMode.PRINT_READY
    limit: int | None = Field(default=None, ge=1)
    min_quality_gain: float = Field(default=0.01, ge=0, le=1)
    min_detail_gain: float = Field(default=0.03, ge=0, le=1)
    max_semantic_drop: float = Field(default=0.02, ge=0, le=1)
    max_latency_ratio: float | None = Field(default=None, ge=1)
    max_cost_per_case_usd: float | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class SRLaneRun(StrictModel):
    lane: SRBenchmarkLane
    available: bool
    run_id: str | None = None
    scorecard_id: str | None = None
    status: HarnessRunStatus | None = None
    unavailable_reason: str | None = None
    case_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    quality_mean: float | None = Field(default=None, ge=0, le=1)
    technical_mean: float | None = Field(default=None, ge=0, le=1)
    small_detail_survival_mean: float | None = Field(default=None, ge=0, le=1)
    latency_p50_ms: float = Field(default=0, ge=0)
    latency_p95_ms: float = Field(default=0, ge=0)
    peak_ram_mb: float = Field(default=0, ge=0)
    peak_vram_mb: float = Field(default=0, ge=0)
    provider_calls: int = Field(default=0, ge=0)
    total_cost_usd: float = Field(default=0, ge=0)
    manual_review_count: int = Field(default=0, ge=0)


class SRLaneCaseEvidence(StrictModel):
    lane: SRBenchmarkLane
    success: bool = False
    quality_score: float | None = Field(default=None, ge=0, le=1)
    semantic_score: float | None = Field(default=None, ge=0, le=1)
    technical_score: float | None = Field(default=None, ge=0, le=1)
    small_detail_survival: float | None = Field(default=None, ge=0, le=1)
    effective_resolution: float | None = Field(default=None, ge=0, le=1)
    latency_ms: float = Field(default=0, ge=0)
    peak_ram_mb: float = Field(default=0, ge=0)
    peak_vram_mb: float = Field(default=0, ge=0)
    provider_calls: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    manual_review: bool = False
    fail_closed: bool = False
    hallucination_risk: bool = False
    reasons: list[str] = Field(default_factory=list)


class SRCaseComparison(StrictModel):
    pair_id: str
    artwork_identity: str
    lanes: list[SRLaneCaseEvidence] = Field(default_factory=list)
    preferred_lane: SRCasePreference = SRCasePreference.UNUSABLE
    quality_gain: float | None = Field(default=None, ge=-1, le=1)
    detail_gain: float | None = Field(default=None, ge=-1, le=1)
    semantic_delta: float | None = Field(default=None, ge=-1, le=1)
    reasons: list[str] = Field(default_factory=list)


class SRBenchmarkReport(StrictModel):
    schema_version: str = SCHEMA_VERSION
    matrix_id: str
    dataset_id: str
    tier: BenchmarkTier
    recipe_id: str
    recipe_version: str
    quality_mode: QualityMode
    min_quality_gain: float = Field(ge=0, le=1)
    min_detail_gain: float = Field(ge=0, le=1)
    max_semantic_drop: float = Field(ge=0, le=1)
    dataset_manifest_sha256: str = ""
    runs: list[SRLaneRun] = Field(default_factory=list)
    comparisons: list[SRCaseComparison] = Field(default_factory=list)
    comparable_case_count: int = Field(default=0, ge=0)
    native_preferred_count: int = Field(default=0, ge=0)
    lanczos_preferred_count: int = Field(default=0, ge=0)
    local_sr_preferred_count: int = Field(default=0, ge=0)
    remote_sr_preferred_count: int = Field(default=0, ge=0)
    tie_count: int = Field(default=0, ge=0)
    incomplete_count: int = Field(default=0, ge=0)
    recommendation: SRBenchmarkRecommendation = (
        SRBenchmarkRecommendation.INSUFFICIENT_EVIDENCE
    )
    requires_human_approval: bool = True
    auto_applied: bool = False
    production_execution_enabled: bool = False
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class SRPolicyRecommendation(StrEnum):
    KEEP_NATIVE = "keep_native"
    KEEP_LANCZOS = "keep_lanczos"
    LOCAL_SR = "local_sr"
    REMOTE_SR = "remote_sr"
    MIXED = "mixed"
    MANUAL_REVIEW = "manual_review"


class SRExperimentSpec(StrictModel):
    schema_version: str = SCHEMA_VERSION
    experiment_id: str = Field(default_factory=lambda: "sr_experiment_" + uuid4().hex)
    dataset_id: str
    pre_sr_manifest_path: str
    recipe_path: str
    tier: BenchmarkTier = BenchmarkTier.GOLDEN
    require_golden: bool = True
    local_adapter_path: str | None = None
    remote_adapter_path: str | None = None
    quality_mode: QualityMode = QualityMode.PRINT_READY
    scale_factor: float = Field(default=2.0, gt=1, le=4)
    max_output_megapixels: float = Field(default=80, gt=0, le=200)
    limit: int | None = Field(default=None, ge=1)
    min_quality_gain: float = Field(default=0.01, ge=0, le=1)
    min_detail_gain: float = Field(default=0.03, ge=0, le=1)
    max_semantic_drop: float = Field(default=0.02, ge=0, le=1)
    max_latency_ratio: float | None = Field(default=None, ge=1)
    max_cost_per_case_usd: float | None = Field(default=None, ge=0)
    min_comparable_cases: int = Field(default=3, ge=1)
    min_decisive_wins: int = Field(default=2, ge=1)
    max_incomplete_rate: float = Field(default=0.0, ge=0, le=1)
    created_at: datetime = Field(default_factory=utc_now)


class SRPolicyProposal(StrictModel):
    schema_version: str = SCHEMA_VERSION
    proposal_id: str = Field(default_factory=lambda: "sr_policy_" + uuid4().hex)
    experiment_id: str
    dataset_id: str
    tier: BenchmarkTier
    dataset_manifest_sha256: str
    cohort_id: str
    cohort_case_count: int = Field(default=0, ge=0)
    cohort_success_count: int = Field(default=0, ge=0)
    comparable_case_count: int = Field(default=0, ge=0)
    incomplete_count: int = Field(default=0, ge=0)
    native_preferred_count: int = Field(default=0, ge=0)
    lanczos_preferred_count: int = Field(default=0, ge=0)
    local_sr_preferred_count: int = Field(default=0, ge=0)
    remote_sr_preferred_count: int = Field(default=0, ge=0)
    tie_count: int = Field(default=0, ge=0)
    local_backend_available: bool = False
    remote_backend_available: bool = False
    min_quality_gain: float = Field(ge=0, le=1)
    min_detail_gain: float = Field(ge=0, le=1)
    max_semantic_drop: float = Field(ge=0, le=1)
    max_latency_ratio: float | None = Field(default=None, ge=1)
    max_cost_per_case_usd: float | None = Field(default=None, ge=0)
    min_comparable_cases: int = Field(default=3, ge=1)
    min_decisive_wins: int = Field(default=2, ge=1)
    max_incomplete_rate: float = Field(default=0.0, ge=0, le=1)
    recommendation: SRPolicyRecommendation = SRPolicyRecommendation.MANUAL_REVIEW
    sufficient_evidence: bool = False
    requires_human_approval: bool = True
    automatically_applied: bool = False
    production_execution_enabled: bool = False
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class SRExperimentReport(StrictModel):
    schema_version: str = SCHEMA_VERSION
    experiment_id: str
    dataset_id: str
    tier: BenchmarkTier
    cohort_id: str
    matrix_id: str
    cohort_report_path: str
    local_adapter_report_path: str | None = None
    remote_adapter_report_path: str | None = None
    matrix_report_path: str
    policy_proposal_path: str
    policy: SRPolicyProposal
    benchmark_only: bool = True
    requires_human_approval: bool = True
    auto_applied: bool = False
    production_execution_enabled: bool = False
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class RegistrationPolicyRecommendation(StrEnum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    AFFINE_FOR_DEWARP_BENCHMARK = "affine_for_dewarp_benchmark"
    HOMOGRAPHY_FOR_DEWARP_BENCHMARK = "homography_for_dewarp_benchmark"
    MIXED_FOR_DEWARP_BENCHMARK = "mixed_for_dewarp_benchmark"
    MANUAL_REVIEW = "manual_review"


class RegistrationThresholds(StrictModel):
    min_match_count: int = Field(default=6, ge=3)
    min_inlier_count: int = Field(default=5, ge=3)
    min_inlier_ratio: float = Field(default=0.60, ge=0, le=1)
    min_spatial_coverage: float = Field(default=0.18, ge=0, le=1)
    max_mean_reprojection_error: float = Field(default=0.025, ge=0)
    max_median_reprojection_error: float = Field(default=0.018, ge=0)
    max_homography_error_ratio: float | None = Field(default=None, ge=0)


class RegistrationLaneCalibration(StrictModel):
    lane: str
    case_count: int = Field(default=0, ge=0)
    accepted_reference_count: int = Field(default=0, ge=0)
    sample_count: int = Field(default=0, ge=0)
    sufficient_evidence: bool = False
    thresholds: RegistrationThresholds | None = None
    inlier_ratio_p10: float | None = Field(default=None, ge=0, le=1)
    spatial_coverage_p10: float | None = Field(default=None, ge=0, le=1)
    mean_reprojection_error_p90: float | None = Field(default=None, ge=0)
    median_reprojection_error_p90: float | None = Field(default=None, ge=0)
    homography_error_ratio_p90: float | None = Field(default=None, ge=0)
    reasons: list[str] = Field(default_factory=list)


class RegistrationPolicyProposal(StrictModel):
    schema_version: str = SCHEMA_VERSION
    proposal_id: str = Field(default_factory=lambda: "registration_policy_" + uuid4().hex)
    method: str = "golden_registration_calibration_v1"
    dataset_id: str
    source_run_ids: list[str] = Field(min_length=1)
    source_scorecard_ids: list[str] = Field(default_factory=list)
    source_recipe_ids: list[str] = Field(default_factory=list)
    source_recipe_versions: list[str] = Field(default_factory=list)
    source_tiers: list[BenchmarkTier] = Field(default_factory=list)
    dataset_manifest_sha256: str = ""
    total_case_count: int = Field(default=0, ge=0)
    successful_case_count: int = Field(default=0, ge=0)
    measured_reference_count: int = Field(default=0, ge=0)
    insufficient_feature_count: int = Field(default=0, ge=0)
    manual_review_count: int = Field(default=0, ge=0)
    semantic_required_count: int = Field(default=0, ge=0)
    manual_review_rate: float = Field(default=0, ge=0, le=1)
    max_manual_review_rate: float = Field(default=0.15, ge=0, le=1)
    min_cases_per_lane: int = Field(default=3, ge=1)
    affine: RegistrationLaneCalibration
    homography: RegistrationLaneCalibration
    recommendation: RegistrationPolicyRecommendation = (
        RegistrationPolicyRecommendation.INSUFFICIENT_EVIDENCE
    )
    sufficient_evidence: bool = False
    requires_human_approval: bool = True
    automatically_applied: bool = False
    production_execution_enabled: bool = False
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class DewarpRegionCellEvidence(StrictModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    native_similarity: float = Field(ge=0, le=1)
    dewarped_similarity: float = Field(ge=0, le=1)
    delta: float = Field(ge=-1, le=1)


class DewarpCaseEvidence(StrictModel):
    pair_id: str
    case_id: str
    artwork_identity: str
    success: bool = False
    reference_index: int | None = Field(default=None, ge=0)
    model: str = ""
    source_path: str = ""
    primary_path: str = ""
    native_path: str | None = None
    dewarped_path: str | None = None
    native_region_mean: float | None = Field(default=None, ge=0, le=1)
    dewarped_region_mean: float | None = Field(default=None, ge=0, le=1)
    region_mean_delta: float | None = Field(default=None, ge=-1, le=1)
    improved_region_cells: int = Field(default=0, ge=0)
    region_cells: list[DewarpRegionCellEvidence] = Field(default_factory=list)
    fail_closed: bool = False
    reasons: list[str] = Field(default_factory=list)


class DewarpMaterializationSpec(StrictModel):
    schema_version: str = SCHEMA_VERSION
    cohort_id: str = Field(default_factory=lambda: "dewarp_cohort_" + uuid4().hex)
    dataset_id: str
    source_run_id: str
    registration_policy_path: str
    tier: BenchmarkTier = BenchmarkTier.GOLDEN
    canonical_size: int = Field(default=512, ge=128, le=2048)
    limit: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utc_now)


class DewarpMaterializationReport(StrictModel):
    schema_version: str = SCHEMA_VERSION
    cohort_id: str
    dataset_id: str
    source_run_id: str
    registration_policy_id: str
    tier: BenchmarkTier
    dataset_manifest_sha256: str = ""
    native_manifest_path: str
    dewarp_manifest_path: str
    case_count: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)
    affine_count: int = Field(default=0, ge=0)
    homography_count: int = Field(default=0, ge=0)
    region_improved_case_count: int = Field(default=0, ge=0)
    cases: list[DewarpCaseEvidence] = Field(default_factory=list)
    benchmark_only: bool = True
    requires_human_approval: bool = True
    automatically_applied: bool = False
    production_execution_enabled: bool = False
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class DewarpBenchmarkRecommendation(StrEnum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    KEEP_NATIVE = "keep_native"
    DEWARP_FOR_HUMAN_REVIEW = "dewarp_for_human_review"
    MANUAL_REVIEW = "manual_review"


class DewarpBenchmarkSpec(StrictModel):
    schema_version: str = SCHEMA_VERSION
    matrix_id: str = Field(default_factory=lambda: "dewarp_matrix_" + uuid4().hex)
    dataset_id: str
    tier: BenchmarkTier = BenchmarkTier.GOLDEN
    recipe_path: str
    native_manifest_path: str
    dewarp_manifest_path: str
    min_quality_gain: float = Field(default=0.005, ge=0, le=1)
    min_technical_gain: float = Field(default=0.0, ge=0, le=1)
    min_comparable_cases: int = Field(default=3, ge=1)
    max_failure_rate_increase: float = Field(default=0.0, ge=0, le=1)
    max_manual_review_rate_increase: float = Field(default=0.0, ge=0, le=1)
    limit: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utc_now)


class DewarpBenchmarkReport(StrictModel):
    schema_version: str = SCHEMA_VERSION
    matrix_id: str
    dataset_id: str
    tier: BenchmarkTier
    recipe_id: str
    recipe_version: str
    native_run_id: str
    dewarp_run_id: str
    native_scorecard_id: str
    dewarp_scorecard_id: str
    comparable_case_count: int = Field(default=0, ge=0)
    quality_delta: float | None = Field(default=None, ge=-1, le=1)
    technical_delta: float | None = Field(default=None, ge=-1, le=1)
    small_detail_delta: float | None = Field(default=None, ge=-1, le=1)
    failure_rate_delta: float = Field(default=0, ge=-1, le=1)
    manual_review_rate_delta: float = Field(default=0, ge=-1, le=1)
    recommendation: DewarpBenchmarkRecommendation = (
        DewarpBenchmarkRecommendation.INSUFFICIENT_EVIDENCE
    )
    sufficient_evidence: bool = False
    benchmark_only: bool = True
    requires_human_approval: bool = True
    automatically_applied: bool = False
    production_execution_enabled: bool = False
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class RouterThresholdCalibrationMetric(StrictModel):
    metric: str
    sample_count: int = Field(ge=0)
    prefer_remote_count: int = Field(ge=0)
    prefer_deterministic_count: int = Field(ge=0)
    current_threshold: float = Field(ge=0, le=1)
    recommended_threshold: float | None = Field(default=None, ge=0, le=1)
    false_local_rate: float | None = Field(default=None, ge=0, le=1)
    unnecessary_remote_rate: float | None = Field(default=None, ge=0, le=1)
    sufficient_evidence: bool = False
    reasons: list[str] = Field(default_factory=list)


class RouterPolicyCalibrationProposal(StrictModel):
    schema_version: str = SCHEMA_VERSION
    proposal_id: str = Field(default_factory=lambda: "router_cal_" + uuid4().hex)
    source_matrix_ids: list[str] = Field(min_length=1)
    source_tiers: list[BenchmarkTier] = Field(default_factory=list)
    current_policy: RouterPolicy
    candidate_policy: RouterPolicy
    metrics: dict[str, RouterThresholdCalibrationMetric] = Field(default_factory=dict)
    min_quality_gain: float = Field(default=0.02, ge=0, le=1)
    sufficient_evidence: bool = False
    requires_human_approval: bool = True
    automatically_applied: bool = False
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class CandidateManifestEntry(StrictModel):
    result_path: str
    recognized_text: list[str] = Field(default_factory=list)
    semantic_judge: SemanticJudgeResult | None = None
    operational: OperationalMetrics = Field(default_factory=OperationalMetrics)
    precision: PrecisionEvidence = Field(default_factory=PrecisionEvidence)
    route: RouteEvidence = Field(default_factory=RouteEvidence)
    runtime_qc: RuntimeQCEvidence = Field(default_factory=RuntimeQCEvidence)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateManifest(StrictModel):
    schema_version: str = SCHEMA_VERSION
    candidates: dict[str, CandidateManifestEntry]

    @model_validator(mode="after")
    def validate_candidates(self) -> "CandidateManifest":
        if not self.candidates:
            raise ValueError("candidate manifest must contain at least one candidate")
        return self
