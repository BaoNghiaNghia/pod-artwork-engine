from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = "1.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class QualityMode(StrEnum):
    QUICK_2D = "quick_2d"
    PRINT_READY = "print_ready"
    MAX_FIDELITY = "max_fidelity"


class JobState(StrEnum):
    QUEUED = "queued"
    PREFLIGHT = "preflight"
    ANALYZING = "analyzing"
    RECONSTRUCTING = "reconstructing"
    PRECISION_FINISHING = "precision_finishing"
    QC = "qc"
    REGION_RESCUE = "region_rescue"
    REVIEW_REQUIRED = "review_required"
    COMPLETED = "completed"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_FINAL = "failed_final"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    RESUMING = "resuming"
    WAITING_PROVIDER = "waiting_provider"
    WAITING_COMPUTE = "waiting_compute"
    BLOCKED_BUDGET = "blocked_budget"


class FailureCategory(StrEnum):
    SOURCE_ERROR = "source_error"
    PROVIDER_ERROR = "provider_error"
    COMPUTE_ERROR = "compute_error"
    QUALITY_FAILURE = "quality_failure"
    POLICY_FAILURE = "policy_failure"
    BUDGET_FAILURE = "budget_failure"
    CANCELLED = "cancelled"


class ArtworkType(StrEnum):
    TYPOGRAPHY = "typography"
    LOGO = "logo"
    ILLUSTRATION = "illustration"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class HistoricalAssetRole(StrEnum):
    SOURCE = "source"
    TARGET = "target"


class DatasetSplit(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    GOLDEN_HOLDOUT = "golden_holdout"


class RouteKind(StrEnum):
    DETERMINISTIC = "deterministic"
    REMOTE_SEMANTIC = "remote_semantic"
    HYBRID = "hybrid"


class ReferenceEvidenceStatus(StrEnum):
    PRIMARY = "primary"
    CONSISTENT = "consistent"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"


class ReferenceAlignmentDisposition(StrEnum):
    IDENTITY = "identity"
    AFFINE_CANDIDATE = "affine_candidate"
    HOMOGRAPHY_CANDIDATE = "homography_candidate"
    SEMANTIC_REQUIRED = "semantic_required"
    MANUAL_REVIEW = "manual_review"


class FeatureCorrespondenceDisposition(StrEnum):
    IDENTITY = "identity"
    MEASURED_AFFINE = "measured_affine"
    MEASURED_HOMOGRAPHY = "measured_homography"
    INSUFFICIENT_FEATURES = "insufficient_features"
    SEMANTIC_REQUIRED = "semantic_required"
    MANUAL_REVIEW = "manual_review"


class RegionRescueDisposition(StrEnum):
    NONE = "none"
    SEMANTIC_PROVIDER_CANDIDATE = "semantic_provider_candidate"
    MANUAL_REVIEW = "manual_review"


class RepresentationKind(StrEnum):
    VECTOR = "vector"
    RASTER = "raster"
    HYBRID = "hybrid"


class RepresentationComponentKind(StrEnum):
    TYPOGRAPHY = "typography"
    GEOMETRY = "geometry"
    ILLUSTRATION_TEXTURE = "illustration_texture"
    UNKNOWN_CONTENT = "unknown_content"


class MaterialSeparationDisposition(StrEnum):
    EXISTING_ALPHA = "existing_alpha"
    SIMPLE_BORDER_BACKGROUND = "simple_border_background"
    SEMANTIC_REQUIRED = "semantic_required"
    MANUAL_REVIEW = "manual_review"


class TextureHandlingDisposition(StrEnum):
    PRESERVE_RASTER = "preserve_raster"
    LOCAL_DETAIL_ENHANCEMENT_CANDIDATE = "local_detail_enhancement_candidate"
    SEMANTIC_REQUIRED = "semantic_required"
    MANUAL_REVIEW = "manual_review"


class SuperResolutionDisposition(StrEnum):
    NATIVE_SUFFICIENT = "native_sufficient"
    LOCAL_SR_CANDIDATE = "local_sr_candidate"
    REMOTE_SR_CANDIDATE = "remote_sr_candidate"
    MANUAL_REVIEW = "manual_review"


class ProviderAction(StrEnum):
    ANALYZE = "analyze"
    RECONSTRUCT = "reconstruct"
    JUDGE = "judge"
    SUPER_RESOLUTION = "super_resolution"


class QCGate(StrEnum):
    SEMANTIC = "semantic"
    TECHNICAL = "technical"


class RegionReplacementMode(StrEnum):
    NONE = "none"
    OVERLAY = "overlay"
    REPLACE_SOLID = "replace_solid"
    REPLACE_MASK = "replace_mask"
    REPAIR_LOCAL = "repair_local"


class GeometryKind(StrEnum):
    RECT = "rect"
    ELLIPSE = "ellipse"
    LINE = "line"
    POLYGON = "polygon"
    PATH = "path"


class PathCommandKind(StrEnum):
    MOVE = "move"
    LINE = "line"
    CUBIC = "cubic"
    CLOSE = "close"


class GeometryFillRule(StrEnum):
    NONZERO = "nonzero"
    EVENODD = "evenodd"


class BoundingBox(StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class NormalizedPoint(StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class FontMatchEvidence(StrictModel):
    family: str = ""
    style: str = ""
    weight: int = Field(default=400, ge=100, le=900)
    score: float = Field(default=0, ge=0, le=1)
    margin: float = Field(default=0, ge=0, le=1)
    accepted: bool = False
    method: str = ""
    candidates_evaluated: int = Field(default=0, ge=0)
    font_sha256: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")


class TypographyLine(StrictModel):
    text: str = Field(min_length=1)
    bbox: BoundingBox
    font_family: str = ""
    font_weight: int = Field(default=400, ge=100, le=900)
    fill: str = "#000000"
    stroke: str | None = None
    stroke_width_ratio: float = Field(default=0, ge=0, le=0.1)
    rotation_degrees: float = Field(default=0, ge=-180, le=180)
    confidence: float = Field(default=0, ge=0, le=1)
    replacement_mode: RegionReplacementMode = RegionReplacementMode.NONE
    replacement_fill: str | None = None
    replacement_mask: list[NormalizedPoint] = Field(default_factory=list)
    replacement_confidence: float = Field(default=0, ge=0, le=1)
    font_match: FontMatchEvidence | None = None


class TypographySpec(StrictModel):
    lines: list[TypographyLine] = Field(default_factory=list)
    line_order_confidence: float = Field(default=0, ge=0, le=1)
    font_match_confidence: float = Field(default=0, ge=0, le=1)
    evidence_provider: str = ""
    evidence_version: str = ""


class GeometryPathCommand(StrictModel):
    kind: PathCommandKind
    points: list[NormalizedPoint] = Field(default_factory=list)


class GeometryPrimitive(StrictModel):
    kind: GeometryKind
    bbox: BoundingBox | None = None
    points: list[NormalizedPoint] = Field(default_factory=list)
    path: list[GeometryPathCommand] = Field(default_factory=list)
    fill: str | None = None
    stroke: str | None = None
    stroke_width_ratio: float = Field(default=0.004, ge=0, le=0.1)
    fill_rule: GeometryFillRule = GeometryFillRule.NONZERO
    confidence: float = Field(default=0, ge=0, le=1)


class GeometrySpec(StrictModel):
    primitives: list[GeometryPrimitive] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    evidence_provider: str = ""
    evidence_version: str = ""


class GeometryTopologyEvidence(StrictModel):
    method: str = "deterministic_geometry_v2"
    evidence_provider: str = ""
    evidence_version: str = ""
    primitive_count: int = Field(default=0, ge=0)
    path_primitive_count: int = Field(default=0, ge=0)
    subpath_count: int = Field(default=0, ge=0)
    compound_path_count: int = Field(default=0, ge=0)
    evenodd_compound_fill_count: int = Field(default=0, ge=0)
    fill_rules: list[GeometryFillRule] = Field(default_factory=list)


class ReferenceEvidence(StrictModel):
    index: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality_score: float = Field(ge=0, le=1)
    similarity_to_primary: float = Field(ge=0, le=1)
    status: ReferenceEvidenceStatus
    reason_codes: list[str] = Field(default_factory=list)


class MultiReferenceFusionEvidence(StrictModel):
    method: str = "deterministic_reference_fusion_v1"
    reference_count: int = Field(ge=1)
    primary_index: int = Field(ge=0)
    consensus_confidence: float = Field(ge=0, le=1)
    conflict_detected: bool = False
    consistent_indices: list[int] = Field(default_factory=list)
    ambiguous_indices: list[int] = Field(default_factory=list)
    conflicting_indices: list[int] = Field(default_factory=list)
    references: list[ReferenceEvidence] = Field(default_factory=list)


class ReferenceAlignmentEvidence(StrictModel):
    index: int = Field(ge=0)
    disposition: ReferenceAlignmentDisposition
    source_bbox: BoundingBox | None = None
    target_bbox: BoundingBox | None = None
    source_quad: list[NormalizedPoint] = Field(
        default_factory=list,
        max_length=4,
    )
    target_quad: list[NormalizedPoint] = Field(
        default_factory=list,
        max_length=4,
    )
    transform_matrix: list[float] = Field(
        default_factory=list,
        max_length=9,
    )
    geometry_confidence: float = Field(default=0, ge=0, le=1)
    aspect_compatibility: float = Field(default=0, ge=0, le=1)
    perspective_severity: float = Field(default=0, ge=0, le=1)
    occlusion: float = Field(default=0, ge=0, le=1)
    reprojection_error: float | None = Field(default=None, ge=0)
    fail_closed: bool = False
    execution_enabled: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)


class MultiReferenceAlignmentEvidence(StrictModel):
    method: str = "geometric_reference_alignment_v1"
    reference_count: int = Field(ge=1)
    primary_index: int = Field(ge=0)
    mean_geometry_confidence: float = Field(default=0, ge=0, le=1)
    aligned_indices: list[int] = Field(default_factory=list)
    excluded_indices: list[int] = Field(default_factory=list)
    affine_candidate_count: int = Field(default=0, ge=0)
    homography_candidate_count: int = Field(default=0, ge=0)
    semantic_required_count: int = Field(default=0, ge=0)
    manual_review_count: int = Field(default=0, ge=0)
    fail_closed: bool = False
    execution_enabled: bool = False
    references: list[ReferenceAlignmentEvidence] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class FeatureMatchEvidence(StrictModel):
    source: NormalizedPoint
    target: NormalizedPoint
    patch_error: float = Field(ge=0, le=1)
    uniqueness_margin: float = Field(default=0, ge=0, le=1)


class ReferenceCorrespondenceEvidence(StrictModel):
    index: int = Field(ge=0)
    disposition: FeatureCorrespondenceDisposition
    model: str = ""
    match_count: int = Field(default=0, ge=0)
    inlier_count: int = Field(default=0, ge=0)
    inlier_ratio: float = Field(default=0, ge=0, le=1)
    spatial_coverage: float = Field(default=0, ge=0, le=1)
    mean_reprojection_error: float | None = Field(default=None, ge=0)
    median_reprojection_error: float | None = Field(default=None, ge=0)
    affine_inlier_ratio: float | None = Field(default=None, ge=0, le=1)
    affine_mean_reprojection_error: float | None = Field(default=None, ge=0)
    affine_median_reprojection_error: float | None = Field(default=None, ge=0)
    homography_inlier_ratio: float | None = Field(default=None, ge=0, le=1)
    homography_mean_reprojection_error: float | None = Field(default=None, ge=0)
    homography_median_reprojection_error: float | None = Field(default=None, ge=0)
    homography_error_ratio: float | None = Field(default=None, ge=0)
    transform_matrix: list[float] = Field(default_factory=list, max_length=9)
    matches: list[FeatureMatchEvidence] = Field(default_factory=list, max_length=32)
    fail_closed: bool = False
    execution_enabled: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)


class MultiReferenceCorrespondenceEvidence(StrictModel):
    method: str = "feature_correspondence_benchmark_v1"
    reference_count: int = Field(ge=1)
    primary_index: int = Field(ge=0)
    measured_indices: list[int] = Field(default_factory=list)
    excluded_indices: list[int] = Field(default_factory=list)
    measured_affine_count: int = Field(default=0, ge=0)
    measured_homography_count: int = Field(default=0, ge=0)
    insufficient_feature_count: int = Field(default=0, ge=0)
    semantic_required_count: int = Field(default=0, ge=0)
    manual_review_count: int = Field(default=0, ge=0)
    mean_inlier_ratio: float = Field(default=0, ge=0, le=1)
    mean_reprojection_error: float | None = Field(default=None, ge=0)
    fail_closed: bool = False
    execution_enabled: bool = False
    references: list[ReferenceCorrespondenceEvidence] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class RegionEvidenceCell(StrictModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    bbox: BoundingBox
    confidence: float = Field(ge=0, le=1)
    agreement: float = Field(ge=0, le=1)
    support_count: int = Field(ge=1)
    comparison_count: int = Field(ge=0)
    reason_codes: list[str] = Field(default_factory=list)


class RegionConfidenceMapEvidence(StrictModel):
    method: str = "normalized_region_evidence_v1"
    rows: int = Field(default=4, ge=1, le=16)
    columns: int = Field(default=4, ge=1, le=16)
    primary_index: int = Field(ge=0)
    supporting_indices: list[int] = Field(default_factory=list)
    excluded_indices: list[int] = Field(default_factory=list)
    mean_confidence: float = Field(ge=0, le=1)
    minimum_confidence: float = Field(ge=0, le=1)
    support_coverage: float = Field(ge=0, le=1)
    low_confidence_cells: int = Field(ge=0)
    cells: list[RegionEvidenceCell] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class RegionCellRef(StrictModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)


class RegionRescueTarget(StrictModel):
    bbox: BoundingBox
    cells: list[RegionCellRef] = Field(default_factory=list)
    mean_confidence: float = Field(ge=0, le=1)
    maximum_confidence: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)


class RegionRescuePlanEvidence(StrictModel):
    method: str = "region_rescue_plan_v1"
    disposition: RegionRescueDisposition = RegionRescueDisposition.NONE
    primary_index: int = Field(ge=0)
    provider_available: bool = False
    fail_closed: bool = False
    target_cell_count: int = Field(default=0, ge=0)
    targets: list[RegionRescueTarget] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class RepresentationComponentPlan(StrictModel):
    component: RepresentationComponentKind
    representation: RepresentationKind
    confidence: float = Field(ge=0, le=1)
    supported: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)


class RepresentationPlanEvidence(StrictModel):
    method: str = "representation_plan_v1"
    overall: RepresentationKind
    confidence: float = Field(ge=0, le=1)
    fail_closed: bool = False
    components: list[RepresentationComponentPlan] = Field(default_factory=list)
    vector_components: int = Field(default=0, ge=0)
    raster_components: int = Field(default=0, ge=0)
    reason_codes: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)


class MaterialSeparationEvidence(StrictModel):
    method: str = "material_separation_evidence_v1"
    disposition: MaterialSeparationDisposition
    primary_index: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    fail_closed: bool = False
    artwork_bbox: BoundingBox | None = None
    source_has_alpha: bool = False
    meaningful_alpha: bool = False
    transparent_fraction: float = Field(default=0, ge=0, le=1)
    visible_fraction: float = Field(default=0, ge=0, le=1)
    border_uniformity: float = Field(default=0, ge=0, le=1)
    edge_contact_ratio: float = Field(default=0, ge=0, le=1)
    foreground_contrast: float = Field(default=0, ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)


class TextureHandlingEvidence(StrictModel):
    method: str = "texture_handling_evidence_v1"
    disposition: TextureHandlingDisposition
    primary_index: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    fail_closed: bool = False
    artwork_bbox: BoundingBox | None = None
    edge_density: float = Field(default=0, ge=0, le=1)
    local_contrast: float = Field(default=0, ge=0, le=1)
    local_variation: float = Field(default=0, ge=0, le=1)
    native_long_edge: int = Field(default=0, ge=0)
    source_quality: float = Field(default=0, ge=0, le=1)
    compression_risk: float = Field(default=0, ge=0, le=1)
    region_mean_confidence: float = Field(default=0, ge=0, le=1)
    material_disposition: MaterialSeparationDisposition
    reason_codes: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)


class SuperResolutionReadinessEvidence(StrictModel):
    method: str = "super_resolution_readiness_v1"
    disposition: SuperResolutionDisposition
    primary_index: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    fail_closed: bool = False
    execution_enabled: bool = False
    provider_available: bool = False
    artwork_bbox: BoundingBox | None = None
    native_artwork_width: int = Field(default=0, ge=0)
    native_artwork_height: int = Field(default=0, ge=0)
    native_long_edge: int = Field(default=0, ge=0)
    target_artwork_width: int = Field(default=0, ge=0)
    target_artwork_height: int = Field(default=0, ge=0)
    target_long_edge: int = Field(default=0, ge=0)
    required_native_long_edge: int = Field(default=0, ge=0)
    estimated_scale_factor: float = Field(default=1, ge=0)
    source_quality: float = Field(default=0, ge=0, le=1)
    compression_risk: float = Field(default=0, ge=0, le=1)
    region_mean_confidence: float = Field(default=0, ge=0, le=1)
    texture_disposition: TextureHandlingDisposition
    material_disposition: MaterialSeparationDisposition
    reason_codes: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)


class SemanticJudgeResult(StrictModel):
    exact_text: float | None = Field(default=None, ge=0, le=1)
    layout: float | None = Field(default=None, ge=0, le=1)
    object_fidelity: float | None = Field(default=None, ge=0, le=1)
    color: float | None = Field(default=None, ge=0, le=1)
    texture: float | None = Field(default=None, ge=0, le=1)
    missing_detail: float | None = Field(default=None, ge=0, le=1)
    confidence: float = Field(default=0, ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)


class DesignSpec(StrictModel):
    schema_version: str = SCHEMA_VERSION
    artwork_type: ArtworkType = ArtworkType.UNKNOWN
    artwork_bbox: BoundingBox | None = None
    exact_text: list[str] = Field(default_factory=list)
    typography: TypographySpec | None = None
    geometry: GeometrySpec | None = None
    objects: list[str] = Field(default_factory=list)
    dominant_colors: list[str] = Field(default_factory=list)
    texture_classes: list[str] = Field(default_factory=list)
    perspective_severity: float = Field(default=0, ge=0, le=1)
    occlusion: float = Field(default=0, ge=0, le=1)
    confidence: float = Field(default=0, ge=0, le=1)
    required_capabilities: list[str] = Field(default_factory=list)


class PreflightResult(StrictModel):
    schema_version: str = SCHEMA_VERSION
    sha256: str
    filename: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    image_mode: str
    image_format: str | None = None
    file_size_bytes: int = Field(ge=0)
    has_alpha: bool = False
    orientation_applied: bool = False
    blur_score: float = Field(default=0, ge=0, le=1)
    compression_risk: float = Field(default=0, ge=0, le=1)
    source_quality: float = Field(default=0, ge=0, le=1)
    artwork_bbox: BoundingBox | None = None
    artwork_confidence: float = Field(default=0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


class RouteDecision(StrictModel):
    schema_version: str = SCHEMA_VERSION
    route: RouteKind
    required_capabilities: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    use_remote_provider: bool = False
    deterministic_finish: bool = True


class ProviderActionRecipe(StrictModel):
    action: ProviderAction
    enabled: bool = True
    model_alias: str = ""
    max_reference_long_edge: int = Field(default=1600, ge=512, le=4096)
    timeout_seconds: float | None = Field(default=None, gt=0, le=600)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ProviderRecipe(StrictModel):
    schema_version: str = SCHEMA_VERSION
    recipe_id: str = "default"
    version: str = "1"
    provider_name: str = "remote"
    actions: list[ProviderActionRecipe] = Field(default_factory=list)

    def for_action(self, action: ProviderAction) -> ProviderActionRecipe | None:
        return next((item for item in self.actions if item.action is action), None)


class ProviderRequest(StrictModel):
    schema_version: str = SCHEMA_VERSION
    action: ProviderAction
    job_id: str
    quality_mode: QualityMode
    source_paths: list[str] = Field(min_length=1)
    candidate_path: str | None = None
    design_spec: DesignSpec | None = None
    requested_capabilities: list[str] = Field(default_factory=list)


class ProviderResult(StrictModel):
    schema_version: str = SCHEMA_VERSION
    provider: str
    model_version: str = ""
    design_spec: DesignSpec | None = None
    candidate_path: str | None = None
    candidate_image_base64: str | None = None
    recognized_text: list[str] = Field(default_factory=list)
    judge_result: SemanticJudgeResult | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class QCResult(StrictModel):
    schema_version: str = SCHEMA_VERSION
    gate: QCGate
    passed: bool
    score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, float | int | str | bool | None] = Field(default_factory=dict)


class ExportProfile(StrictModel):
    schema_version: str = SCHEMA_VERSION
    name: str = "default_pod"
    width: int = Field(default=4500, gt=0)
    height: int = Field(default=5400, gt=0)
    dpi: int = Field(default=300, gt=0)
    format: str = "PNG"
    transparent: bool = True
    max_artwork_width_ratio: float = Field(default=0.84, gt=0, le=1)
    max_artwork_height_ratio: float = Field(default=0.84, gt=0, le=1)


class ResourceSnapshot(StrictModel):
    cpu_percent: float = Field(default=0, ge=0)
    process_rss_bytes: int = Field(default=0, ge=0)
    memory_available_bytes: int = Field(default=0, ge=0)
    storage_used_bytes: int = Field(default=0, ge=0)


class ArtifactRef(StrictModel):
    artifact_id: str = Field(default_factory=lambda: uuid4().hex)
    kind: str
    path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class ArtifactManifest(StrictModel):
    schema_version: str = SCHEMA_VERSION
    job_id: str
    source_hashes: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    model_versions: dict[str, str] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    policy_version: str = "foundation-v1"
    export_profile: str = "default_pod"
    precision_evidence: dict[str, Any] = Field(default_factory=dict)
    qc_report: dict[str, Any] = Field(default_factory=dict)


class JobRecord(StrictModel):
    schema_version: str = SCHEMA_VERSION
    job_id: str = Field(default_factory=lambda: uuid4().hex)
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    quality_mode: QualityMode = QualityMode.PRINT_READY
    state: JobState = JobState.QUEUED
    progress: float = Field(default=0, ge=0, le=1)
    stage_message: str = "Queued"
    source_paths: list[str] = Field(default_factory=list)
    result_path: str | None = None
    failure_category: FailureCategory | None = None
    failure_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class StorageStatus(StrictModel):
    used_bytes: int = Field(ge=0)
    soft_limit_bytes: int = Field(gt=0)
    hard_limit_bytes: int = Field(gt=0)
    cache_bytes: int = Field(ge=0)
    jobs_bytes: int = Field(ge=0)
    logs_bytes: int = Field(ge=0)
    updates_bytes: int = Field(ge=0)
    artifacts_bytes: int = Field(ge=0)
    datasets_bytes: int = Field(ge=0)
    harness_bytes: int = Field(ge=0)
    state: str


class HistoricalAsset(StrictModel):
    schema_version: str = SCHEMA_VERSION
    asset_id: str
    role: HistoricalAssetRole
    path: str
    sha256: str
    normalized_hash: str
    visual_hash: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    file_size_bytes: int = Field(ge=0)
    has_alpha: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class HistoricalPair(StrictModel):
    schema_version: str = SCHEMA_VERSION
    pair_id: str
    pair_key: str
    artwork_identity: str
    target_asset_id: str
    source_asset_ids: list[str] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class DatasetRecord(StrictModel):
    schema_version: str = SCHEMA_VERSION
    dataset_id: str
    name: str
    version: int = Field(ge=1)
    seed: str
    train_ratio: float = Field(ge=0, le=1)
    validation_ratio: float = Field(ge=0, le=1)
    golden_ratio: float = Field(ge=0, le=1)
    pair_count: int = Field(ge=0)
    artwork_count: int = Field(ge=0)
    split_counts: dict[str, int] = Field(default_factory=dict)
    manifest_path: str
    created_at: datetime = Field(default_factory=utc_now)


class DatasetMember(StrictModel):
    schema_version: str = SCHEMA_VERSION
    dataset_id: str
    pair_id: str
    artwork_identity: str
    split: DatasetSplit
    retrieval_eligible: bool
