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
