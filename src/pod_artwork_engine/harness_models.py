from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import Field, model_validator

from .contracts import SCHEMA_VERSION, SemanticJudgeResult, StrictModel, utc_now


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


class BenchmarkPlan(StrictModel):
    schema_version: str = SCHEMA_VERSION
    plan_id: str = Field(default_factory=lambda: "plan_" + uuid4().hex)
    run_id: str
    dataset_id: str
    tier: BenchmarkTier
    recipe_id: str
    recipe_version: str
    case_ids: list[str] = Field(min_length=1)
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
    local_ocr: bool = False
    ocr_backend: str = ""
    typography_rebuilt: bool = False
    mixed_text_refined: bool = False
    geometry_vector: bool = False
    masked_text_regions: int = Field(default=0, ge=0)
    provider_recipe_id: str = ""
    precision_ops: list[str] = Field(default_factory=list)


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
    cohorts: dict[str, CohortScore] = Field(default_factory=dict)
    result_paths: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class PromotionPolicy(StrictModel):
    min_overall_delta: float = 0
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


class CandidateManifestEntry(StrictModel):
    result_path: str
    recognized_text: list[str] = Field(default_factory=list)
    semantic_judge: SemanticJudgeResult | None = None
    operational: OperationalMetrics = Field(default_factory=OperationalMetrics)
    precision: PrecisionEvidence = Field(default_factory=PrecisionEvidence)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateManifest(StrictModel):
    schema_version: str = SCHEMA_VERSION
    candidates: dict[str, CandidateManifestEntry]

    @model_validator(mode="after")
    def validate_candidates(self) -> "CandidateManifest":
        if not self.candidates:
            raise ValueError("candidate manifest must contain at least one candidate")
        return self
