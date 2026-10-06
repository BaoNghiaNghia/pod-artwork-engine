from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from .artwork_detection import crop_artwork
from .contracts import (
    BoundingBox,
    FeatureCorrespondenceDisposition,
    MultiReferenceCorrespondenceEvidence,
    ReferenceCorrespondenceEvidence,
)
from .harness import (
    HarnessCaseFactory,
    HarnessRunner,
    HarnessStore,
    load_candidate_manifest,
    load_recipe,
)
from .harness_models import (
    BenchmarkCaseResult,
    BenchmarkTier,
    CandidateManifest,
    CandidateManifestEntry,
    DewarpBenchmarkRecommendation,
    DewarpBenchmarkReport,
    DewarpBenchmarkSpec,
    DewarpCaseEvidence,
    DewarpMaterializationReport,
    DewarpMaterializationSpec,
    DewarpRegionCellEvidence,
    OperationalMetrics,
    RegistrationPolicyProposal,
    RegistrationPolicyRecommendation,
    RegistrationThresholds,
    RunProvenance,
)
from .preflight import inspect_image
from .region_evidence import GRID_COLUMNS, GRID_ROWS, _block_similarity, _cell_crop
from .settings import Settings


class DewarpBenchmarkError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_registration_policy(path: Path) -> RegistrationPolicyProposal:
    return RegistrationPolicyProposal.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def _matrix_multiply(left: list[float], right: list[float]) -> list[float]:
    if len(left) != 9 or len(right) != 9:
        raise ValueError("3x3 matrix multiplication requires 9 coefficients")
    output = [0.0] * 9
    for row in range(3):
        for column in range(3):
            output[row * 3 + column] = sum(
                left[row * 3 + offset] * right[offset * 3 + column]
                for offset in range(3)
            )
    return output


def _matrix_inverse(matrix: list[float]) -> list[float]:
    if len(matrix) != 9:
        raise ValueError("3x3 matrix inversion requires 9 coefficients")
    a, b, c, d, e, f, g, h, i = matrix
    determinant = (
        a * (e * i - f * h)
        - b * (d * i - f * g)
        + c * (d * h - e * g)
    )
    if abs(determinant) < 1e-12:
        raise ValueError("registration transform is singular")
    inverse = [
        e * i - f * h,
        c * h - b * i,
        b * f - c * e,
        f * g - d * i,
        a * i - c * g,
        c * d - a * f,
        d * h - e * g,
        b * g - a * h,
        a * e - b * d,
    ]
    return [value / determinant for value in inverse]


def _bbox_matrix(bbox: BoundingBox) -> list[float]:
    return [
        bbox.width,
        0.0,
        bbox.x,
        0.0,
        bbox.height,
        bbox.y,
        0.0,
        0.0,
        1.0,
    ]


def _crop_to_crop_matrix(
    full_matrix: list[float],
    source_bbox: BoundingBox,
    target_bbox: BoundingBox,
) -> list[float]:
    source_to_full = _bbox_matrix(source_bbox)
    target_to_full = _bbox_matrix(target_bbox)
    full_to_target = _matrix_inverse(target_to_full)
    return _matrix_multiply(
        _matrix_multiply(full_to_target, full_matrix),
        source_to_full,
    )


def _pixel_inverse_coefficients(
    crop_matrix: list[float],
    width: int,
    height: int,
) -> tuple[float, ...]:
    inverse = _matrix_inverse(crop_matrix)
    sx = max(1.0, float(width - 1))
    sy = max(1.0, float(height - 1))
    scale_to_pixels = [sx, 0.0, 0.0, 0.0, sy, 0.0, 0.0, 0.0, 1.0]
    scale_to_normalized = [
        1.0 / sx,
        0.0,
        0.0,
        0.0,
        1.0 / sy,
        0.0,
        0.0,
        0.0,
        1.0,
    ]
    pixel_matrix = _matrix_multiply(
        _matrix_multiply(scale_to_pixels, inverse),
        scale_to_normalized,
    )
    denominator = pixel_matrix[8]
    if abs(denominator) < 1e-12:
        raise ValueError("registration transform has invalid projective scale")
    normalized = [value / denominator for value in pixel_matrix]
    return tuple(normalized[index] for index in range(8))


def _canonical_crop(
    path: Path,
    bbox: BoundingBox,
    size: int,
) -> Image.Image:
    return crop_artwork(path, bbox).convert("RGBA").resize(
        (size, size),
        Image.Resampling.LANCZOS,
    )


def _warp_reference(
    source: Image.Image,
    crop_matrix: list[float],
) -> Image.Image:
    coefficients = _pixel_inverse_coefficients(
        crop_matrix,
        source.width,
        source.height,
    )
    return source.transform(
        source.size,
        Image.Transform.PERSPECTIVE,
        coefficients,
        resample=Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )


def _region_cells(
    primary: Image.Image,
    native: Image.Image,
    dewarped: Image.Image,
) -> tuple[list[DewarpRegionCellEvidence], float, float]:
    cells: list[DewarpRegionCellEvidence] = []
    native_values: list[float] = []
    dewarped_values: list[float] = []
    for row in range(GRID_ROWS):
        for column in range(GRID_COLUMNS):
            primary_cell = _cell_crop(
                primary.convert("RGB"),
                row,
                column,
                GRID_ROWS,
                GRID_COLUMNS,
            )
            native_cell = _cell_crop(
                native.convert("RGB"),
                row,
                column,
                GRID_ROWS,
                GRID_COLUMNS,
            )
            dewarped_cell = _cell_crop(
                dewarped.convert("RGB"),
                row,
                column,
                GRID_ROWS,
                GRID_COLUMNS,
            )
            native_similarity = _block_similarity(primary_cell, native_cell)
            dewarped_similarity = _block_similarity(primary_cell, dewarped_cell)
            native_values.append(native_similarity)
            dewarped_values.append(dewarped_similarity)
            cells.append(
                DewarpRegionCellEvidence(
                    row=row,
                    column=column,
                    native_similarity=round(native_similarity, 8),
                    dewarped_similarity=round(dewarped_similarity, 8),
                    delta=round(dewarped_similarity - native_similarity, 8),
                )
            )
    return (
        cells,
        sum(native_values) / len(native_values),
        sum(dewarped_values) / len(dewarped_values),
    )


def _policy_lane_allowed(
    policy: RegistrationPolicyProposal,
    reference: ReferenceCorrespondenceEvidence,
) -> tuple[bool, RegistrationThresholds | None, list[str]]:
    reasons: list[str] = []
    if reference.disposition is FeatureCorrespondenceDisposition.MEASURED_AFFINE:
        allowed = policy.recommendation in {
            RegistrationPolicyRecommendation.AFFINE_FOR_DEWARP_BENCHMARK,
            RegistrationPolicyRecommendation.MIXED_FOR_DEWARP_BENCHMARK,
        }
        lane = policy.affine
    elif (
        reference.disposition
        is FeatureCorrespondenceDisposition.MEASURED_HOMOGRAPHY
    ):
        allowed = policy.recommendation in {
            RegistrationPolicyRecommendation.HOMOGRAPHY_FOR_DEWARP_BENCHMARK,
            RegistrationPolicyRecommendation.MIXED_FOR_DEWARP_BENCHMARK,
        }
        lane = policy.homography
    else:
        return False, None, ["reference_is_not_measured_registration"]

    if not allowed:
        reasons.append("registration_lane_not_authorized_by_policy")
    if not lane.sufficient_evidence or lane.thresholds is None:
        reasons.append("registration_lane_policy_is_incomplete")
        return False, lane.thresholds, reasons

    thresholds = lane.thresholds
    if reference.match_count < thresholds.min_match_count:
        reasons.append("match_count_below_policy_threshold")
    if reference.inlier_count < thresholds.min_inlier_count:
        reasons.append("inlier_count_below_policy_threshold")
    if reference.inlier_ratio < thresholds.min_inlier_ratio:
        reasons.append("inlier_ratio_below_policy_threshold")
    if reference.spatial_coverage < thresholds.min_spatial_coverage:
        reasons.append("spatial_coverage_below_policy_threshold")
    if (
        reference.mean_reprojection_error is None
        or reference.mean_reprojection_error
        > thresholds.max_mean_reprojection_error
    ):
        reasons.append("mean_reprojection_error_exceeds_policy_threshold")
    if (
        reference.median_reprojection_error is None
        or reference.median_reprojection_error
        > thresholds.max_median_reprojection_error
    ):
        reasons.append("median_reprojection_error_exceeds_policy_threshold")
    if (
        reference.disposition
        is FeatureCorrespondenceDisposition.MEASURED_HOMOGRAPHY
        and thresholds.max_homography_error_ratio is not None
        and (
            reference.homography_error_ratio is None
            or reference.homography_error_ratio
            > thresholds.max_homography_error_ratio
        )
    ):
        reasons.append("homography_improvement_below_policy_threshold")
    if len(reference.transform_matrix) != 9:
        reasons.append("measured_transform_matrix_unavailable")
    return not reasons, thresholds, reasons


def _validate_policy(
    policy: RegistrationPolicyProposal,
    *,
    dataset_id: str,
    source_run_id: str,
) -> None:
    reasons: list[str] = []
    if policy.dataset_id != dataset_id:
        reasons.append("registration policy dataset mismatch")
    if source_run_id not in policy.source_run_ids:
        reasons.append("source run is not part of registration policy provenance")
    if not policy.source_tiers or any(
        tier is not BenchmarkTier.GOLDEN for tier in policy.source_tiers
    ):
        reasons.append("registration policy is not Golden Holdout derived")
    if not policy.sufficient_evidence:
        reasons.append("registration policy has insufficient evidence")
    if not policy.requires_human_approval:
        reasons.append("registration policy must require human approval")
    if policy.automatically_applied:
        reasons.append("automatically applied registration policy is forbidden")
    if policy.production_execution_enabled:
        reasons.append("production registration execution must remain disabled")
    if policy.recommendation in {
        RegistrationPolicyRecommendation.INSUFFICIENT_EVIDENCE,
        RegistrationPolicyRecommendation.MANUAL_REVIEW,
    }:
        reasons.append("registration policy does not authorize benchmark dewarp")
    if reasons:
        raise DewarpBenchmarkError("; ".join(reasons))


class DewarpMaterializer:
    """Materialize native and dewarped reference crops for Golden benchmarking only."""

    def __init__(self, settings: Settings, registry, store: HarnessStore) -> None:
        self.settings = settings
        self.registry = registry
        self.store = store
        self.case_factory = HarnessCaseFactory(registry)

    @staticmethod
    def _failure_entry(path: Path, reason: str) -> CandidateManifestEntry:
        return CandidateManifestEntry(
            result_path=str(path),
            operational=OperationalMetrics(manual_review=True),
            metadata={
                "benchmark_only": True,
                "production_execution_enabled": False,
                "fail_closed": True,
                "failure_reason": reason,
                "reason_codes": ["dewarp_materialization_failure"],
            },
        )

    def materialize(
        self,
        spec: DewarpMaterializationSpec,
    ) -> DewarpMaterializationReport:
        if spec.tier is not BenchmarkTier.GOLDEN:
            raise DewarpBenchmarkError("dewarp materialization requires Golden Holdout tier")
        policy_path = Path(spec.registration_policy_path).expanduser().resolve()
        if not policy_path.is_file():
            raise FileNotFoundError(
                f"registration policy proposal not found: {policy_path}"
            )
        policy = load_registration_policy(policy_path)
        _validate_policy(
            policy,
            dataset_id=spec.dataset_id,
            source_run_id=spec.source_run_id,
        )

        scorecard = self.store.get_scorecard(spec.source_run_id)
        if scorecard is None:
            raise KeyError(f"scorecard not found: {spec.source_run_id}")
        if scorecard.tier is not BenchmarkTier.GOLDEN:
            raise DewarpBenchmarkError("source registration run must be Golden Holdout")
        if scorecard.dataset_id != spec.dataset_id:
            raise DewarpBenchmarkError("source registration run dataset mismatch")
        if (
            policy.dataset_manifest_sha256
            and scorecard.provenance.dataset_manifest_sha256
            and policy.dataset_manifest_sha256
            != scorecard.provenance.dataset_manifest_sha256
        ):
            raise DewarpBenchmarkError("registration policy fingerprint mismatch")

        cases = self.case_factory.build(
            spec.dataset_id,
            BenchmarkTier.GOLDEN,
            limit=spec.limit,
        )
        if not cases:
            raise DewarpBenchmarkError("dataset has no Golden Holdout cases")
        results = {
            result.case_id: result
            for result in self.store.get_results(spec.source_run_id)
        }
        cohort_dir = self.store.dewarp_cohort_dir(spec.cohort_id)
        native_dir = cohort_dir / "native"
        dewarp_dir = cohort_dir / "dewarp"
        native_manifest_path = cohort_dir / "native-candidates.json"
        dewarp_manifest_path = cohort_dir / "dewarp-candidates.json"

        native_entries: dict[str, CandidateManifestEntry] = {}
        dewarp_entries: dict[str, CandidateManifestEntry] = {}
        case_evidence: list[DewarpCaseEvidence] = []
        success_count = 0
        affine_count = 0
        homography_count = 0
        improved_cases = 0

        for case in cases:
            native_path = native_dir / f"{case.pair_id}.png"
            dewarp_path = dewarp_dir / f"{case.pair_id}.png"
            result = results.get(case.case_id)
            reason: str | None = None
            evidence = None
            if result is None:
                reason = "source Golden case result unavailable"
            else:
                raw = result.metadata.get("feature_correspondence")
                if not isinstance(raw, dict) or not raw:
                    reason = "feature correspondence evidence unavailable"
                else:
                    try:
                        evidence = MultiReferenceCorrespondenceEvidence.model_validate(raw)
                    except ValueError as exc:
                        reason = f"invalid feature correspondence evidence: {exc}"

            selected: ReferenceCorrespondenceEvidence | None = None
            baseline_selected: ReferenceCorrespondenceEvidence | None = None
            if reason is None and evidence is not None:
                measured = [
                    reference
                    for reference in evidence.references
                    if (
                        reference.index != evidence.primary_index
                        and reference.disposition
                        in {
                            FeatureCorrespondenceDisposition.MEASURED_AFFINE,
                            FeatureCorrespondenceDisposition.MEASURED_HOMOGRAPHY,
                        }
                    )
                ]
                if measured:
                    baseline_selected = sorted(
                        measured,
                        key=lambda item: (
                            -item.inlier_ratio,
                            item.median_reprojection_error
                            if item.median_reprojection_error is not None
                            else 1e9,
                        ),
                    )[0]

                eligible: list[ReferenceCorrespondenceEvidence] = []
                for reference in measured:
                    allowed, _, _ = _policy_lane_allowed(policy, reference)
                    if allowed:
                        eligible.append(reference)
                if eligible:
                    selected = sorted(
                        eligible,
                        key=lambda item: (
                            -item.inlier_ratio,
                            item.median_reprojection_error
                            if item.median_reprojection_error is not None
                            else 1e9,
                        ),
                    )[0]
                else:
                    reason = "no reference satisfies registration policy thresholds"

            if (
                evidence is not None
                and (
                    evidence.primary_index >= len(case.source_paths)
                    or (
                        baseline_selected is not None
                        and baseline_selected.index >= len(case.source_paths)
                    )
                )
            ):
                reason = "registration reference index is outside case source paths"
                selected = None
                baseline_selected = None

            if reason is not None or evidence is None or selected is None:
                native_saved = False
                native_reference = baseline_selected
                if evidence is not None and native_reference is not None:
                    try:
                        source_path = Path(
                            case.source_paths[native_reference.index]
                        ).resolve()
                        source_preflight = inspect_image(source_path)
                        if source_preflight.artwork_bbox is None:
                            raise DewarpBenchmarkError(
                                "artwork bbox unavailable for native baseline"
                            )
                        source_image = _canonical_crop(
                            source_path,
                            source_preflight.artwork_bbox,
                            spec.canonical_size,
                        )
                        native_path.parent.mkdir(parents=True, exist_ok=True)
                        source_image.save(
                            native_path,
                            format="PNG",
                            compress_level=6,
                        )
                        native_entries[case.pair_id] = CandidateManifestEntry(
                            result_path=str(native_path),
                            metadata={
                                "benchmark_only": True,
                                "production_execution_enabled": False,
                                "fail_closed": False,
                                "dewarp_lane": "native_unregistered",
                                "dewarp_cohort": {
                                    "cohort_id": spec.cohort_id,
                                    "policy_id": policy.proposal_id,
                                    "source_run_id": spec.source_run_id,
                                    "reference_index": native_reference.index,
                                    "model": native_reference.model,
                                },
                            },
                        )
                        native_saved = True
                    except (OSError, ValueError, DewarpBenchmarkError):
                        native_saved = False

                if not native_saved:
                    native_entries[case.pair_id] = self._failure_entry(
                        native_path,
                        reason or "unavailable",
                    )
                dewarp_entries[case.pair_id] = self._failure_entry(
                    dewarp_path,
                    reason or "unavailable",
                )
                case_evidence.append(
                    DewarpCaseEvidence(
                        pair_id=case.pair_id,
                        case_id=case.case_id,
                        artwork_identity=case.artwork_identity,
                        reference_index=(
                            baseline_selected.index
                            if baseline_selected is not None
                            else None
                        ),
                        model=(
                            baseline_selected.model
                            if baseline_selected is not None
                            else ""
                        ),
                        native_path=(
                            str(native_path)
                            if native_saved
                            else None
                        ),
                        fail_closed=True,
                        reasons=[reason or "dewarp materialization unavailable"],
                    )
                )
                continue

            primary_path = Path(case.source_paths[evidence.primary_index]).resolve()
            source_path = Path(case.source_paths[selected.index]).resolve()
            try:
                primary_preflight = inspect_image(primary_path)
                source_preflight = inspect_image(source_path)
                if (
                    primary_preflight.artwork_bbox is None
                    or source_preflight.artwork_bbox is None
                ):
                    raise DewarpBenchmarkError("artwork bbox unavailable for dewarp")
                primary_image = _canonical_crop(
                    primary_path,
                    primary_preflight.artwork_bbox,
                    spec.canonical_size,
                )
                source_image = _canonical_crop(
                    source_path,
                    source_preflight.artwork_bbox,
                    spec.canonical_size,
                )
                crop_matrix = _crop_to_crop_matrix(
                    selected.transform_matrix,
                    source_preflight.artwork_bbox,
                    primary_preflight.artwork_bbox,
                )
                dewarped_image = _warp_reference(source_image, crop_matrix)

                native_path.parent.mkdir(parents=True, exist_ok=True)
                dewarp_path.parent.mkdir(parents=True, exist_ok=True)
                source_image.save(native_path, format="PNG", compress_level=6)
                dewarped_image.save(dewarp_path, format="PNG", compress_level=6)

                cells, native_mean, dewarp_mean = _region_cells(
                    primary_image,
                    source_image,
                    dewarped_image,
                )
                improved = sum(cell.delta > 0 for cell in cells)
                if dewarp_mean > native_mean:
                    improved_cases += 1

                common_metadata = {
                    "benchmark_only": True,
                    "production_execution_enabled": False,
                    "fail_closed": False,
                    "dewarp_cohort": {
                        "cohort_id": spec.cohort_id,
                        "policy_id": policy.proposal_id,
                        "source_run_id": spec.source_run_id,
                        "reference_index": selected.index,
                        "model": selected.model,
                        "native_region_mean": round(native_mean, 8),
                        "dewarped_region_mean": round(dewarp_mean, 8),
                        "region_mean_delta": round(dewarp_mean - native_mean, 8),
                    },
                }
                native_entries[case.pair_id] = CandidateManifestEntry(
                    result_path=str(native_path),
                    metadata={
                        **common_metadata,
                        "dewarp_lane": "native_unregistered",
                    },
                )
                dewarp_entries[case.pair_id] = CandidateManifestEntry(
                    result_path=str(dewarp_path),
                    metadata={
                        **common_metadata,
                        "dewarp_lane": "registered_dewarp",
                    },
                )
                case_evidence.append(
                    DewarpCaseEvidence(
                        pair_id=case.pair_id,
                        case_id=case.case_id,
                        artwork_identity=case.artwork_identity,
                        success=True,
                        reference_index=selected.index,
                        model=selected.model,
                        source_path=str(source_path),
                        primary_path=str(primary_path),
                        native_path=str(native_path),
                        dewarped_path=str(dewarp_path),
                        native_region_mean=round(native_mean, 8),
                        dewarped_region_mean=round(dewarp_mean, 8),
                        region_mean_delta=round(dewarp_mean - native_mean, 8),
                        improved_region_cells=improved,
                        region_cells=cells,
                    )
                )
                success_count += 1
                if (
                    selected.disposition
                    is FeatureCorrespondenceDisposition.MEASURED_HOMOGRAPHY
                ):
                    homography_count += 1
                else:
                    affine_count += 1
            except (OSError, ValueError, DewarpBenchmarkError) as exc:
                failure_reason = f"{type(exc).__name__}: {exc}"
                native_entries[case.pair_id] = self._failure_entry(
                    native_path,
                    failure_reason,
                )
                dewarp_entries[case.pair_id] = self._failure_entry(
                    dewarp_path,
                    failure_reason,
                )
                case_evidence.append(
                    DewarpCaseEvidence(
                        pair_id=case.pair_id,
                        case_id=case.case_id,
                        artwork_identity=case.artwork_identity,
                        reference_index=selected.index,
                        model=selected.model,
                        source_path=str(source_path),
                        primary_path=str(primary_path),
                        fail_closed=True,
                        reasons=[failure_reason],
                    )
                )

        self.store.save_model(
            native_manifest_path,
            CandidateManifest(candidates=native_entries),
        )
        self.store.save_model(
            dewarp_manifest_path,
            CandidateManifest(candidates=dewarp_entries),
        )
        dataset = self.registry.get_dataset(spec.dataset_id)
        fingerprint = ""
        if dataset is not None and Path(dataset.manifest_path).is_file():
            fingerprint = _sha256(Path(dataset.manifest_path))

        report = DewarpMaterializationReport(
            cohort_id=spec.cohort_id,
            dataset_id=spec.dataset_id,
            source_run_id=spec.source_run_id,
            registration_policy_id=policy.proposal_id,
            tier=BenchmarkTier.GOLDEN,
            dataset_manifest_sha256=fingerprint,
            native_manifest_path=str(native_manifest_path),
            dewarp_manifest_path=str(dewarp_manifest_path),
            case_count=len(cases),
            success_count=success_count,
            failure_count=len(cases) - success_count,
            affine_count=affine_count,
            homography_count=homography_count,
            region_improved_case_count=improved_cases,
            cases=case_evidence,
            reasons=["production_dewarp_execution_disabled"],
        )
        self.store.save_model(cohort_dir / "report.json", report)
        return report


class DewarpBenchmarkMatrixRunner:
    def __init__(self, settings: Settings, registry, store: HarnessStore) -> None:
        self.settings = settings
        self.registry = registry
        self.store = store

    @staticmethod
    def _validate_manifest(
        manifest: CandidateManifest,
        *,
        expected_lane: str,
    ) -> None:
        for key, entry in manifest.candidates.items():
            if entry.metadata.get("benchmark_only") is not True:
                raise DewarpBenchmarkError(
                    f"dewarp matrix candidate {key} is not benchmark-only"
                )
            if entry.metadata.get("production_execution_enabled") is True:
                raise DewarpBenchmarkError(
                    f"dewarp matrix candidate {key} enables production execution"
                )
            if entry.metadata.get("fail_closed") is True:
                continue
            if entry.metadata.get("dewarp_lane") != expected_lane:
                raise DewarpBenchmarkError(
                    f"dewarp matrix candidate {key} lane mismatch"
                )

    @staticmethod
    def _cohort_signature(
        result: BenchmarkCaseResult,
    ) -> tuple[str, str, int] | None:
        raw = result.metadata.get("dewarp_cohort")
        if not isinstance(raw, dict):
            return None
        cohort_id = raw.get("cohort_id")
        source_run_id = raw.get("source_run_id")
        reference_index = raw.get("reference_index")
        if not isinstance(cohort_id, str) or not cohort_id:
            return None
        if not isinstance(source_run_id, str) or not source_run_id:
            return None
        if not isinstance(reference_index, int):
            return None
        return cohort_id, source_run_id, reference_index

    @classmethod
    def _same_source(
        cls,
        native: BenchmarkCaseResult,
        dewarped: BenchmarkCaseResult,
    ) -> bool:
        native_signature = cls._cohort_signature(native)
        dewarp_signature = cls._cohort_signature(dewarped)
        return (
            native_signature is not None
            and dewarp_signature is not None
            and native_signature == dewarp_signature
        )

    @staticmethod
    def _mean(values: list[float | None]) -> float | None:
        available = [value for value in values if value is not None]
        return sum(available) / len(available) if available else None

    def run(
        self,
        spec: DewarpBenchmarkSpec,
        *,
        spec_base: Path | None = None,
    ) -> DewarpBenchmarkReport:
        if spec.tier is not BenchmarkTier.GOLDEN:
            raise DewarpBenchmarkError("dewarp matrix requires Golden Holdout tier")
        base = spec_base or Path.cwd()
        recipe_path = Path(spec.recipe_path).expanduser()
        native_path = Path(spec.native_manifest_path).expanduser()
        dewarp_path = Path(spec.dewarp_manifest_path).expanduser()
        if not recipe_path.is_absolute():
            recipe_path = base / recipe_path
        if not native_path.is_absolute():
            native_path = base / native_path
        if not dewarp_path.is_absolute():
            dewarp_path = base / dewarp_path
        recipe = load_recipe(recipe_path.resolve())
        native_manifest = load_candidate_manifest(native_path.resolve())
        dewarp_manifest = load_candidate_manifest(dewarp_path.resolve())
        self._validate_manifest(
            native_manifest,
            expected_lane="native_unregistered",
        )
        self._validate_manifest(
            dewarp_manifest,
            expected_lane="registered_dewarp",
        )

        native_score = HarnessRunner(self.registry, self.store).run_candidates(
            spec.dataset_id,
            BenchmarkTier.GOLDEN,
            recipe.model_copy(
                deep=True,
                update={"recipe_id": f"{recipe.recipe_id}__dewarp_native"},
            ),
            native_manifest,
            manifest_base=native_path.resolve().parent,
            limit=spec.limit,
            provenance=RunProvenance(
                execution_kind="dewarp_benchmark:native_unregistered"
            ),
        )
        dewarp_score = HarnessRunner(self.registry, self.store).run_candidates(
            spec.dataset_id,
            BenchmarkTier.GOLDEN,
            recipe.model_copy(
                deep=True,
                update={"recipe_id": f"{recipe.recipe_id}__dewarp_registered"},
            ),
            dewarp_manifest,
            manifest_base=dewarp_path.resolve().parent,
            limit=spec.limit,
            provenance=RunProvenance(
                execution_kind="dewarp_benchmark:registered_dewarp"
            ),
        )
        native_results = {
            result.case_id: result
            for result in self.store.get_results(native_score.run_id)
        }
        dewarp_results = {
            result.case_id: result
            for result in self.store.get_results(dewarp_score.run_id)
        }
        comparable = [
            (native_results[case_id], dewarp_results[case_id])
            for case_id in sorted(set(native_results) & set(dewarp_results))
            if native_results[case_id].success
            and dewarp_results[case_id].success
            and self._same_source(
                native_results[case_id],
                dewarp_results[case_id],
            )
            and native_results[case_id].quality_score is not None
            and dewarp_results[case_id].quality_score is not None
        ]
        quality_delta = self._mean(
            [
                dewarp.quality_score - native.quality_score
                for native, dewarp in comparable
                if native.quality_score is not None
                and dewarp.quality_score is not None
            ]
        )
        technical_delta = self._mean(
            [
                dewarp.technical_score - native.technical_score
                for native, dewarp in comparable
                if native.technical_score is not None
                and dewarp.technical_score is not None
            ]
        )
        detail_delta = self._mean(
            [
                dewarp.technical.small_detail_survival
                - native.technical.small_detail_survival
                for native, dewarp in comparable
                if native.technical.small_detail_survival is not None
                and dewarp.technical.small_detail_survival is not None
            ]
        )
        failure_delta = dewarp_score.failure_rate - native_score.failure_rate
        manual_delta = (
            dewarp_score.manual_review_rate - native_score.manual_review_rate
        )
        reasons: list[str] = []
        if len(comparable) < spec.min_comparable_cases:
            reasons.append("insufficient_comparable_golden_cases")
        if failure_delta > spec.max_failure_rate_increase:
            reasons.append("dewarp_failure_rate_regression")
        if manual_delta > spec.max_manual_review_rate_increase:
            reasons.append("dewarp_manual_review_rate_regression")
        if quality_delta is None or quality_delta < spec.min_quality_gain:
            reasons.append("dewarp_quality_gain_below_floor")
        if technical_delta is None or technical_delta < spec.min_technical_gain:
            reasons.append("dewarp_technical_gain_below_floor")
        if detail_delta is not None and detail_delta < 0:
            reasons.append("dewarp_small_detail_regression")

        safety_regression = any(
            reason in {
                "dewarp_failure_rate_regression",
                "dewarp_manual_review_rate_regression",
                "dewarp_small_detail_regression",
            }
            for reason in reasons
        )
        enough = len(comparable) >= spec.min_comparable_cases
        gains_ok = (
            quality_delta is not None
            and quality_delta >= spec.min_quality_gain
            and technical_delta is not None
            and technical_delta >= spec.min_technical_gain
        )
        if safety_regression:
            recommendation = DewarpBenchmarkRecommendation.MANUAL_REVIEW
        elif enough and gains_ok:
            recommendation = (
                DewarpBenchmarkRecommendation.DEWARP_FOR_HUMAN_REVIEW
            )
        elif enough:
            recommendation = DewarpBenchmarkRecommendation.KEEP_NATIVE
        else:
            recommendation = (
                DewarpBenchmarkRecommendation.INSUFFICIENT_EVIDENCE
            )

        if (
            native_score.provenance.dataset_manifest_sha256
            != dewarp_score.provenance.dataset_manifest_sha256
        ):
            raise DewarpBenchmarkError(
                "native/dewarp Harness dataset fingerprint mismatch"
            )

        report = DewarpBenchmarkReport(
            matrix_id=spec.matrix_id,
            dataset_id=spec.dataset_id,
            tier=BenchmarkTier.GOLDEN,
            recipe_id=recipe.recipe_id,
            recipe_version=recipe.version,
            dataset_manifest_sha256=(
                native_score.provenance.dataset_manifest_sha256
            ),
            native_run_id=native_score.run_id,
            dewarp_run_id=dewarp_score.run_id,
            native_scorecard_id=native_score.scorecard_id,
            dewarp_scorecard_id=dewarp_score.scorecard_id,
            comparable_case_count=len(comparable),
            quality_delta=(
                round(quality_delta, 8) if quality_delta is not None else None
            ),
            technical_delta=(
                round(technical_delta, 8)
                if technical_delta is not None
                else None
            ),
            small_detail_delta=(
                round(detail_delta, 8) if detail_delta is not None else None
            ),
            failure_rate_delta=round(failure_delta, 8),
            manual_review_rate_delta=round(manual_delta, 8),
            recommendation=recommendation,
            sufficient_evidence=enough and not safety_regression,
            reasons=list(dict.fromkeys(reasons + ["production_dewarp_execution_disabled"])),
        )
        matrix_dir = self.store.dewarp_matrix_dir(spec.matrix_id)
        self.store.save_model(matrix_dir / "report.json", report)
        return report
