from __future__ import annotations

import math
from itertools import combinations
from pathlib import Path
from statistics import median

from PIL import Image

from .artwork_detection import crop_artwork
from .contracts import (
    FeatureCorrespondenceDisposition,
    FeatureMatchEvidence,
    MultiReferenceAlignmentEvidence,
    MultiReferenceCorrespondenceEvidence,
    MultiReferenceFusionEvidence,
    NormalizedPoint,
    PreflightResult,
    ReferenceAlignmentDisposition,
    ReferenceCorrespondenceEvidence,
    ReferenceEvidenceStatus,
)


CANONICAL_SIZE = 96
PATCH_RADIUS = 3
SEARCH_RADIUS = 12
SEARCH_STEP = 2
GRID_POSITIONS = (16, 32, 48, 64, 80)
MIN_PATCH_STD = 8.0
MAX_PATCH_ERROR = 0.20
MIN_UNIQUENESS_MARGIN = 0.012
MAX_MEASURED_MATCHES = 16
MAX_MODEL_MATCHES = 12
MIN_MATCH_COUNT = 6
MIN_SPATIAL_COVERAGE = 0.18
REPROJECTION_INLIER_THRESHOLD = 0.025
MIN_AFFINE_INLIERS = 5
MIN_HOMOGRAPHY_INLIERS = 6
MIN_AFFINE_INLIER_RATIO = 0.60
MIN_HOMOGRAPHY_INLIER_RATIO = 0.65
MAX_AFFINE_MEDIAN_ERROR = 0.018
MAX_AFFINE_MEAN_ERROR = 0.025
MAX_HOMOGRAPHY_MEDIAN_ERROR = 0.015
MAX_HOMOGRAPHY_MEAN_ERROR = 0.023
HOMOGRAPHY_IMPROVEMENT_RATIO = 0.82


def _canonical_gray(path: Path, preflight: PreflightResult) -> Image.Image:
    if preflight.artwork_bbox is None:
        raise ValueError("artwork bounding box is unavailable for feature correspondence")
    crop = crop_artwork(path, preflight.artwork_bbox).convert("L")
    return crop.resize(
        (CANONICAL_SIZE, CANONICAL_SIZE),
        Image.Resampling.LANCZOS,
    )


def _patch_values(
    image: Image.Image,
    x: int,
    y: int,
    radius: int = PATCH_RADIUS,
) -> tuple[list[float], float, float] | None:
    if (
        x - radius < 0
        or y - radius < 0
        or x + radius >= image.width
        or y + radius >= image.height
    ):
        return None
    pixels = image.load()
    values: list[float] = []
    for row in range(y - radius, y + radius + 1):
        for column in range(x - radius, x + radius + 1):
            values.append(float(pixels[column, row]))
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return values, mean, math.sqrt(max(0.0, variance))


def _patch_error(
    source_values: list[float],
    source_mean: float,
    target_values: list[float],
    target_mean: float,
) -> float:
    error = sum(
        abs(
            (source_value - source_mean)
            - (target_value - target_mean)
        )
        for source_value, target_value in zip(
            source_values,
            target_values,
            strict=True,
        )
    )
    return max(0.0, min(1.0, error / (len(source_values) * 255.0)))


def _best_patch_match(
    source: Image.Image,
    target: Image.Image,
    x: int,
    y: int,
) -> tuple[int, int, float, float] | None:
    source_patch = _patch_values(source, x, y)
    if source_patch is None:
        return None
    source_values, source_mean, source_std = source_patch
    if source_std < MIN_PATCH_STD:
        return None

    candidates: list[tuple[float, int, int]] = []
    start_x = max(PATCH_RADIUS, x - SEARCH_RADIUS)
    end_x = min(target.width - PATCH_RADIUS - 1, x + SEARCH_RADIUS)
    start_y = max(PATCH_RADIUS, y - SEARCH_RADIUS)
    end_y = min(target.height - PATCH_RADIUS - 1, y + SEARCH_RADIUS)

    for target_y in range(start_y, end_y + 1, SEARCH_STEP):
        for target_x in range(start_x, end_x + 1, SEARCH_STEP):
            target_patch = _patch_values(target, target_x, target_y)
            if target_patch is None:
                continue
            target_values, target_mean, target_std = target_patch
            if target_std < MIN_PATCH_STD * 0.70:
                continue
            error = _patch_error(
                source_values,
                source_mean,
                target_values,
                target_mean,
            )
            candidates.append((error, target_x, target_y))

    if len(candidates) < 2:
        return None
    candidates.sort(key=lambda item: (item[0], item[2], item[1]))
    best_error, target_x, target_y = candidates[0]
    second_error = candidates[1][0]
    margin = max(0.0, min(1.0, second_error - best_error))
    if best_error > MAX_PATCH_ERROR or margin < MIN_UNIQUENESS_MARGIN:
        return None
    return target_x, target_y, best_error, margin


def _to_full_point(
    preflight: PreflightResult,
    x: int,
    y: int,
) -> NormalizedPoint:
    bbox = preflight.artwork_bbox
    if bbox is None:
        raise ValueError("artwork bounding box is unavailable")
    u = x / max(1, CANONICAL_SIZE - 1)
    v = y / max(1, CANONICAL_SIZE - 1)
    return NormalizedPoint(
        x=max(0.0, min(1.0, bbox.x + u * bbox.width)),
        y=max(0.0, min(1.0, bbox.y + v * bbox.height)),
    )


def measure_feature_matches(
    source_path: Path,
    target_path: Path,
    source_preflight: PreflightResult,
    target_preflight: PreflightResult,
) -> list[FeatureMatchEvidence]:
    source = _canonical_gray(source_path, source_preflight)
    target = _canonical_gray(target_path, target_preflight)
    measured: list[FeatureMatchEvidence] = []

    for y in GRID_POSITIONS:
        for x in GRID_POSITIONS:
            match = _best_patch_match(source, target, x, y)
            if match is None:
                continue
            target_x, target_y, error, margin = match
            measured.append(
                FeatureMatchEvidence(
                    source=_to_full_point(source_preflight, x, y),
                    target=_to_full_point(target_preflight, target_x, target_y),
                    patch_error=round(error, 8),
                    uniqueness_margin=round(margin, 8),
                )
            )

    measured.sort(
        key=lambda item: (
            item.patch_error,
            -item.uniqueness_margin,
            item.source.y,
            item.source.x,
        )
    )
    return measured[:MAX_MEASURED_MATCHES]


def _solve_linear_system(
    matrix: list[list[float]],
    vector: list[float],
) -> list[float] | None:
    size = len(vector)
    if size == 0 or len(matrix) != size:
        return None
    augmented = [
        [float(value) for value in row] + [float(vector[index])]
        for index, row in enumerate(matrix)
    ]
    if any(len(row) != size + 1 for row in augmented):
        return None

    for column in range(size):
        pivot = max(
            range(column, size),
            key=lambda row: abs(augmented[row][column]),
        )
        if abs(augmented[pivot][column]) < 1e-10:
            return None
        if pivot != column:
            augmented[column], augmented[pivot] = (
                augmented[pivot],
                augmented[column],
            )
        pivot_value = augmented[column][column]
        for index in range(column, size + 1):
            augmented[column][index] /= pivot_value
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if abs(factor) < 1e-14:
                continue
            for index in range(column, size + 1):
                augmented[row][index] -= factor * augmented[column][index]

    return [augmented[row][size] for row in range(size)]


def _least_squares(
    rows: list[list[float]],
    values: list[float],
    parameter_count: int,
) -> list[float] | None:
    if len(rows) < parameter_count or len(rows) != len(values):
        return None
    normal = [
        [0.0 for _ in range(parameter_count)]
        for _ in range(parameter_count)
    ]
    rhs = [0.0 for _ in range(parameter_count)]
    for row, value in zip(rows, values, strict=True):
        for left in range(parameter_count):
            rhs[left] += row[left] * value
            for right in range(parameter_count):
                normal[left][right] += row[left] * row[right]
    return _solve_linear_system(normal, rhs)


def _fit_affine(
    matches: list[FeatureMatchEvidence],
) -> list[float] | None:
    if len(matches) < 3:
        return None
    rows: list[list[float]] = []
    values: list[float] = []
    for match in matches:
        x = match.source.x
        y = match.source.y
        tx = match.target.x
        ty = match.target.y
        rows.extend(
            [
                [x, y, 1.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, x, y, 1.0],
            ]
        )
        values.extend([tx, ty])
    params = _least_squares(rows, values, 6)
    if params is None:
        return None
    a, b, c, d, e, f = params
    matrix = [a, b, c, d, e, f, 0.0, 0.0, 1.0]
    if not _reasonable_matrix(matrix):
        return None
    return [round(value, 12) for value in matrix]


def _fit_homography(
    matches: list[FeatureMatchEvidence],
) -> list[float] | None:
    if len(matches) < 4:
        return None
    rows: list[list[float]] = []
    values: list[float] = []
    for match in matches:
        x = match.source.x
        y = match.source.y
        tx = match.target.x
        ty = match.target.y
        rows.extend(
            [
                [x, y, 1.0, 0.0, 0.0, 0.0, -x * tx, -y * tx],
                [0.0, 0.0, 0.0, x, y, 1.0, -x * ty, -y * ty],
            ]
        )
        values.extend([tx, ty])
    params = _least_squares(rows, values, 8)
    if params is None:
        return None
    matrix = [*params, 1.0]
    if not _reasonable_matrix(matrix):
        return None
    return [round(value, 12) for value in matrix]


def _reasonable_matrix(matrix: list[float]) -> bool:
    if len(matrix) != 9:
        return False
    if any(not math.isfinite(value) or abs(value) > 25.0 for value in matrix):
        return False
    return abs(matrix[8]) > 1e-8


def _project(matrix: list[float], point: NormalizedPoint) -> tuple[float, float] | None:
    x = point.x
    y = point.y
    denominator = matrix[6] * x + matrix[7] * y + matrix[8]
    if abs(denominator) < 1e-8:
        return None
    tx = (matrix[0] * x + matrix[1] * y + matrix[2]) / denominator
    ty = (matrix[3] * x + matrix[4] * y + matrix[5]) / denominator
    if not math.isfinite(tx) or not math.isfinite(ty):
        return None
    return tx, ty


def _errors(
    matrix: list[float],
    matches: list[FeatureMatchEvidence],
) -> list[float]:
    errors: list[float] = []
    for match in matches:
        projected = _project(matrix, match.source)
        if projected is None:
            errors.append(float("inf"))
            continue
        tx, ty = projected
        errors.append(
            math.hypot(
                tx - match.target.x,
                ty - match.target.y,
            )
        )
    return errors


def _spatial_coverage(
    matches: list[FeatureMatchEvidence],
    preflight: PreflightResult,
) -> float:
    if not matches or preflight.artwork_bbox is None:
        return 0.0
    xs = [item.source.x for item in matches]
    ys = [item.source.y for item in matches]
    bbox = preflight.artwork_bbox
    width = (max(xs) - min(xs)) / max(bbox.width, 1e-6)
    height = (max(ys) - min(ys)) / max(bbox.height, 1e-6)
    raw = width * height
    return max(0.0, min(1.0, raw / 0.45))


def _ransac_fit(
    matches: list[FeatureMatchEvidence],
    *,
    model: str,
) -> tuple[list[float] | None, list[int], float | None, float | None]:
    usable = matches[:MAX_MODEL_MATCHES]
    sample_size = 3 if model == "affine" else 4
    fitter = _fit_affine if model == "affine" else _fit_homography
    if len(usable) < sample_size:
        return None, [], None, None

    best_matrix: list[float] | None = None
    best_inliers: list[int] = []
    best_median = float("inf")
    best_mean = float("inf")

    for sample_indices in combinations(range(len(usable)), sample_size):
        sample = [usable[index] for index in sample_indices]
        candidate = fitter(sample)
        if candidate is None:
            continue
        candidate_errors = _errors(candidate, usable)
        inliers = [
            index
            for index, error in enumerate(candidate_errors)
            if error <= REPROJECTION_INLIER_THRESHOLD
        ]
        if len(inliers) < sample_size:
            continue
        inlier_errors = [candidate_errors[index] for index in inliers]
        current_median = median(inlier_errors)
        current_mean = sum(inlier_errors) / len(inlier_errors)
        score = (
            len(inliers),
            -current_median,
            -current_mean,
        )
        best_score = (
            len(best_inliers),
            -best_median,
            -best_mean,
        )
        if score > best_score:
            best_matrix = candidate
            best_inliers = inliers
            best_median = current_median
            best_mean = current_mean

    if best_matrix is None or not best_inliers:
        return None, [], None, None

    refined = fitter([usable[index] for index in best_inliers])
    if refined is not None:
        refined_errors = _errors(refined, usable)
        refined_inliers = [
            index
            for index, error in enumerate(refined_errors)
            if error <= REPROJECTION_INLIER_THRESHOLD
        ]
        if len(refined_inliers) >= sample_size:
            best_matrix = refined
            best_inliers = refined_inliers
            inlier_errors = [refined_errors[index] for index in refined_inliers]
            best_median = median(inlier_errors)
            best_mean = sum(inlier_errors) / len(inlier_errors)

    return (
        best_matrix,
        best_inliers,
        round(best_mean, 8),
        round(best_median, 8),
    )


def _alignment_lookup(
    alignment: MultiReferenceAlignmentEvidence,
):
    return {item.index: item for item in alignment.references}


def _fusion_lookup(fusion: MultiReferenceFusionEvidence):
    return {item.index: item for item in fusion.references}


def _identity_reference(index: int) -> ReferenceCorrespondenceEvidence:
    return ReferenceCorrespondenceEvidence(
        index=index,
        disposition=FeatureCorrespondenceDisposition.IDENTITY,
        model="identity",
        match_count=0,
        inlier_count=0,
        inlier_ratio=1.0,
        spatial_coverage=1.0,
        mean_reprojection_error=0.0,
        median_reprojection_error=0.0,
        transform_matrix=[
            1.0, 0.0, 0.0,
            0.0, 1.0, 0.0,
            0.0, 0.0, 1.0,
        ],
        fail_closed=False,
        execution_enabled=False,
        reason_codes=["primary_reference_identity"],
    )


def build_feature_correspondence_evidence(
    source_paths: list[Path],
    preflights: list[PreflightResult],
    fusion: MultiReferenceFusionEvidence,
    alignment: MultiReferenceAlignmentEvidence,
) -> MultiReferenceCorrespondenceEvidence:
    if (
        not source_paths
        or len(source_paths) != len(preflights)
        or len(source_paths) != fusion.reference_count
        or len(source_paths) != alignment.reference_count
    ):
        raise ValueError(
            "feature correspondence requires matching paths, preflights, fusion and alignment"
        )

    primary_index = fusion.primary_index
    if not 0 <= primary_index < len(source_paths):
        raise ValueError("feature correspondence primary index is out of range")

    alignment_by_index = _alignment_lookup(alignment)
    fusion_by_index = _fusion_lookup(fusion)
    references: list[ReferenceCorrespondenceEvidence] = []
    measured_indices: list[int] = [primary_index]
    excluded_indices: list[int] = []
    measured_ratios: list[float] = []
    measured_errors: list[float] = []
    aggregate_reasons: list[str] = []

    affine_count = 0
    homography_count = 0
    insufficient_count = 0
    semantic_count = 0
    manual_count = 0

    for index, (path, preflight) in enumerate(
        zip(source_paths, preflights, strict=True)
    ):
        if index == primary_index:
            references.append(_identity_reference(index))
            continue

        alignment_item = alignment_by_index.get(index)
        fusion_item = fusion_by_index.get(index)
        if alignment_item is None or fusion_item is None:
            references.append(
                ReferenceCorrespondenceEvidence(
                    index=index,
                    disposition=FeatureCorrespondenceDisposition.MANUAL_REVIEW,
                    fail_closed=True,
                    reason_codes=["alignment_or_fusion_evidence_missing"],
                    missing_capabilities=["need_reference_evidence"],
                )
            )
            excluded_indices.append(index)
            manual_count += 1
            aggregate_reasons.append("alignment_or_fusion_evidence_missing")
            continue

        if fusion_item.status is not ReferenceEvidenceStatus.CONSISTENT:
            references.append(
                ReferenceCorrespondenceEvidence(
                    index=index,
                    disposition=FeatureCorrespondenceDisposition.MANUAL_REVIEW,
                    fail_closed=True,
                    reason_codes=["non_consistent_reference_excluded"],
                    missing_capabilities=["need_reference_disambiguation"],
                )
            )
            excluded_indices.append(index)
            manual_count += 1
            aggregate_reasons.append("non_consistent_reference_excluded")
            continue

        if alignment_item.disposition is ReferenceAlignmentDisposition.SEMANTIC_REQUIRED:
            references.append(
                ReferenceCorrespondenceEvidence(
                    index=index,
                    disposition=FeatureCorrespondenceDisposition.SEMANTIC_REQUIRED,
                    fail_closed=True,
                    reason_codes=["alignment_requires_semantic_registration"],
                    missing_capabilities=["need_semantic_registration"],
                )
            )
            excluded_indices.append(index)
            semantic_count += 1
            aggregate_reasons.append("alignment_requires_semantic_registration")
            continue

        if alignment_item.disposition is ReferenceAlignmentDisposition.MANUAL_REVIEW:
            references.append(
                ReferenceCorrespondenceEvidence(
                    index=index,
                    disposition=FeatureCorrespondenceDisposition.MANUAL_REVIEW,
                    fail_closed=True,
                    reason_codes=["alignment_requires_manual_review"],
                    missing_capabilities=["need_manual_registration_review"],
                )
            )
            excluded_indices.append(index)
            manual_count += 1
            aggregate_reasons.append("alignment_requires_manual_review")
            continue

        if (
            preflight.artwork_bbox is None
            or preflights[primary_index].artwork_bbox is None
        ):
            references.append(
                ReferenceCorrespondenceEvidence(
                    index=index,
                    disposition=FeatureCorrespondenceDisposition.MANUAL_REVIEW,
                    fail_closed=True,
                    reason_codes=["artwork_bbox_unavailable"],
                    missing_capabilities=["need_artwork_localization"],
                )
            )
            excluded_indices.append(index)
            manual_count += 1
            aggregate_reasons.append("artwork_bbox_unavailable")
            continue

        try:
            matches = measure_feature_matches(
                path,
                source_paths[primary_index],
                preflight,
                preflights[primary_index],
            )
        except (OSError, ValueError):
            matches = []

        coverage = _spatial_coverage(matches, preflight)
        if len(matches) < MIN_MATCH_COUNT or coverage < MIN_SPATIAL_COVERAGE:
            reasons = []
            if len(matches) < MIN_MATCH_COUNT:
                reasons.append("insufficient_feature_matches")
            if coverage < MIN_SPATIAL_COVERAGE:
                reasons.append("insufficient_spatial_coverage")
            references.append(
                ReferenceCorrespondenceEvidence(
                    index=index,
                    disposition=FeatureCorrespondenceDisposition.INSUFFICIENT_FEATURES,
                    match_count=len(matches),
                    spatial_coverage=round(coverage, 8),
                    matches=matches,
                    fail_closed=True,
                    reason_codes=reasons,
                    missing_capabilities=[
                        "need_more_geometric_features",
                        "need_manual_registration_review",
                    ],
                )
            )
            excluded_indices.append(index)
            insufficient_count += 1
            aggregate_reasons.extend(reasons)
            continue

        affine_matrix, affine_inliers, affine_mean, affine_median = _ransac_fit(
            matches,
            model="affine",
        )
        homography_matrix, homography_inliers, homography_mean, homography_median = (
            _ransac_fit(
                matches,
                model="homography",
            )
        )

        affine_ratio = len(affine_inliers) / max(1, min(len(matches), MAX_MODEL_MATCHES))
        homography_ratio = len(homography_inliers) / max(
            1,
            min(len(matches), MAX_MODEL_MATCHES),
        )
        affine_valid = (
            affine_matrix is not None
            and len(affine_inliers) >= MIN_AFFINE_INLIERS
            and affine_ratio >= MIN_AFFINE_INLIER_RATIO
            and affine_mean is not None
            and affine_median is not None
            and affine_mean <= MAX_AFFINE_MEAN_ERROR
            and affine_median <= MAX_AFFINE_MEDIAN_ERROR
        )
        homography_valid = (
            homography_matrix is not None
            and len(homography_inliers) >= MIN_HOMOGRAPHY_INLIERS
            and homography_ratio >= MIN_HOMOGRAPHY_INLIER_RATIO
            and homography_mean is not None
            and homography_median is not None
            and homography_mean <= MAX_HOMOGRAPHY_MEAN_ERROR
            and homography_median <= MAX_HOMOGRAPHY_MEDIAN_ERROR
        )

        homography_error_ratio = (
            homography_median / affine_median
            if (
                homography_median is not None
                and affine_median is not None
                and affine_median > 1e-9
            )
            else None
        )

        prefer_homography = (
            alignment_item.disposition
            is ReferenceAlignmentDisposition.HOMOGRAPHY_CANDIDATE
            and homography_valid
            and (
                not affine_valid
                or (
                    affine_median is not None
                    and homography_median is not None
                    and homography_median
                    <= affine_median * HOMOGRAPHY_IMPROVEMENT_RATIO
                )
            )
        )

        if prefer_homography:
            references.append(
                ReferenceCorrespondenceEvidence(
                    index=index,
                    disposition=FeatureCorrespondenceDisposition.MEASURED_HOMOGRAPHY,
                    model="homography",
                    match_count=len(matches),
                    inlier_count=len(homography_inliers),
                    inlier_ratio=round(homography_ratio, 8),
                    spatial_coverage=round(coverage, 8),
                    affine_inlier_ratio=round(affine_ratio, 8),
                    affine_mean_reprojection_error=affine_mean,
                    affine_median_reprojection_error=affine_median,
                    homography_inlier_ratio=round(homography_ratio, 8),
                    homography_mean_reprojection_error=homography_mean,
                    homography_median_reprojection_error=homography_median,
                    homography_error_ratio=(
                        round(homography_error_ratio, 8)
                        if homography_error_ratio is not None
                        else None
                    ),
                    mean_reprojection_error=homography_mean,
                    median_reprojection_error=homography_median,
                    transform_matrix=homography_matrix or [],
                    matches=matches,
                    fail_closed=False,
                    execution_enabled=False,
                    reason_codes=[
                        "measured_projective_correspondence",
                        "production_warp_disabled",
                    ],
                    missing_capabilities=[
                        "need_golden_holdout_registration_benchmark",
                        "need_warp_execution_policy",
                    ],
                )
            )
            measured_indices.append(index)
            homography_count += 1
            measured_ratios.append(homography_ratio)
            if homography_mean is not None:
                measured_errors.append(homography_mean)
            continue

        if affine_valid:
            references.append(
                ReferenceCorrespondenceEvidence(
                    index=index,
                    disposition=FeatureCorrespondenceDisposition.MEASURED_AFFINE,
                    model="affine",
                    match_count=len(matches),
                    inlier_count=len(affine_inliers),
                    inlier_ratio=round(affine_ratio, 8),
                    spatial_coverage=round(coverage, 8),
                    affine_inlier_ratio=round(affine_ratio, 8),
                    affine_mean_reprojection_error=affine_mean,
                    affine_median_reprojection_error=affine_median,
                    homography_inlier_ratio=round(homography_ratio, 8),
                    homography_mean_reprojection_error=homography_mean,
                    homography_median_reprojection_error=homography_median,
                    homography_error_ratio=(
                        round(homography_error_ratio, 8)
                        if homography_error_ratio is not None
                        else None
                    ),
                    mean_reprojection_error=affine_mean,
                    median_reprojection_error=affine_median,
                    transform_matrix=affine_matrix or [],
                    matches=matches,
                    fail_closed=False,
                    execution_enabled=False,
                    reason_codes=[
                        "measured_affine_correspondence",
                        "production_warp_disabled",
                    ],
                    missing_capabilities=[
                        "need_golden_holdout_registration_benchmark",
                        "need_warp_execution_policy",
                    ],
                )
            )
            measured_indices.append(index)
            affine_count += 1
            measured_ratios.append(affine_ratio)
            if affine_mean is not None:
                measured_errors.append(affine_mean)
            continue

        reasons = ["reprojection_gate_failed"]
        if homography_valid and alignment_item.disposition is not ReferenceAlignmentDisposition.HOMOGRAPHY_CANDIDATE:
            reasons.append("projective_fit_not_authorized_by_alignment_evidence")
        references.append(
            ReferenceCorrespondenceEvidence(
                index=index,
                disposition=FeatureCorrespondenceDisposition.MANUAL_REVIEW,
                model=(
                    "homography"
                    if homography_matrix is not None
                    else "affine"
                    if affine_matrix is not None
                    else ""
                ),
                match_count=len(matches),
                inlier_count=max(len(affine_inliers), len(homography_inliers)),
                inlier_ratio=round(max(affine_ratio, homography_ratio), 8),
                spatial_coverage=round(coverage, 8),
                affine_inlier_ratio=round(affine_ratio, 8),
                affine_mean_reprojection_error=affine_mean,
                affine_median_reprojection_error=affine_median,
                homography_inlier_ratio=round(homography_ratio, 8),
                homography_mean_reprojection_error=homography_mean,
                homography_median_reprojection_error=homography_median,
                homography_error_ratio=(
                    round(homography_error_ratio, 8)
                    if homography_error_ratio is not None
                    else None
                ),
                mean_reprojection_error=(
                    homography_mean
                    if homography_mean is not None
                    else affine_mean
                ),
                median_reprojection_error=(
                    homography_median
                    if homography_median is not None
                    else affine_median
                ),
                matches=matches,
                fail_closed=True,
                reason_codes=reasons,
                missing_capabilities=[
                    "need_manual_registration_review",
                    "need_better_reference_geometry",
                ],
            )
        )
        excluded_indices.append(index)
        manual_count += 1
        aggregate_reasons.extend(reasons)

    mean_ratio = (
        sum(measured_ratios) / len(measured_ratios)
        if measured_ratios
        else 0.0
    )
    mean_error = (
        sum(measured_errors) / len(measured_errors)
        if measured_errors
        else None
    )
    fail_closed = bool(
        insufficient_count
        or semantic_count
        or manual_count
        or fusion.conflict_detected
    )
    reason_codes = list(dict.fromkeys(aggregate_reasons))
    if len(source_paths) == 1:
        reason_codes.append("single_reference_identity_only")
    if affine_count or homography_count:
        reason_codes.append("measured_correspondence_is_evidence_only")
    if homography_count:
        reason_codes.append("homography_execution_disabled")

    return MultiReferenceCorrespondenceEvidence(
        reference_count=len(source_paths),
        primary_index=primary_index,
        measured_indices=sorted(set(measured_indices)),
        excluded_indices=sorted(set(excluded_indices)),
        measured_affine_count=affine_count,
        measured_homography_count=homography_count,
        insufficient_feature_count=insufficient_count,
        semantic_required_count=semantic_count,
        manual_review_count=manual_count,
        mean_inlier_ratio=round(mean_ratio, 8),
        mean_reprojection_error=(
            round(mean_error, 8)
            if mean_error is not None
            else None
        ),
        fail_closed=fail_closed,
        execution_enabled=False,
        references=sorted(references, key=lambda item: item.index),
        reason_codes=reason_codes,
    )
