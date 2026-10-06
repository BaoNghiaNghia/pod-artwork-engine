from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageOps, ImageStat

from .artwork_detection import crop_artwork
from .contracts import (
    BoundingBox,
    MultiReferenceFusionEvidence,
    PreflightResult,
    RegionConfidenceMapEvidence,
    RegionEvidenceCell,
)
from .multi_reference import reference_quality_score


GRID_ROWS = 4
GRID_COLUMNS = 4
CANONICAL_SIZE = (160, 160)
ASPECT_COMPATIBILITY_MIN = 0.90
REGION_SUPPORT_SIMILARITY = 0.58
LOW_CONFIDENCE_THRESHOLD = 0.60


def _crop_aspect_ratio(preflight: PreflightResult) -> float | None:
    bbox = preflight.artwork_bbox
    if bbox is None:
        return None
    width = max(1.0, preflight.width * bbox.width)
    height = max(1.0, preflight.height * bbox.height)
    return width / height


def _aspect_compatibility(
    first: PreflightResult,
    second: PreflightResult,
) -> float:
    first_ratio = _crop_aspect_ratio(first)
    second_ratio = _crop_aspect_ratio(second)
    if first_ratio is None or second_ratio is None:
        return 0.0
    return min(first_ratio, second_ratio) / max(
        first_ratio,
        second_ratio,
        1e-6,
    )


def _canonical_crop(path: Path, preflight: PreflightResult) -> Image.Image:
    if preflight.artwork_bbox is None:
        raise ValueError("artwork bounding box is unavailable for region evidence")
    crop = crop_artwork(path, preflight.artwork_bbox).convert("RGB")
    return crop.resize(CANONICAL_SIZE, Image.Resampling.LANCZOS)


def _difference_hash(image: Image.Image) -> int:
    gray = ImageOps.fit(
        image.convert("L"),
        (9, 8),
        method=Image.Resampling.LANCZOS,
    )
    pixels = list(gray.getdata())
    value = 0
    bit = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            if pixels[offset + column] > pixels[offset + column + 1]:
                value |= 1 << bit
            bit += 1
    return value


def _block_similarity(first: Image.Image, second: Image.Image) -> float:
    first_hash = _difference_hash(first)
    second_hash = _difference_hash(second)
    hamming = (first_hash ^ second_hash).bit_count()
    shape_similarity = 1.0 - (hamming / 64.0)

    first_mean = ImageStat.Stat(first).mean[:3]
    second_mean = ImageStat.Stat(second).mean[:3]
    color_distance = math.sqrt(
        sum(
            (first_mean[channel] - second_mean[channel]) ** 2
            for channel in range(3)
        )
    )
    color_similarity = 1.0 - min(
        1.0,
        color_distance / (math.sqrt(3.0) * 255.0),
    )
    return max(
        0.0,
        min(1.0, 0.65 * shape_similarity + 0.35 * color_similarity),
    )


def _cell_crop(
    image: Image.Image,
    row: int,
    column: int,
    rows: int,
    columns: int,
) -> Image.Image:
    left = round(column * image.width / columns)
    top = round(row * image.height / rows)
    right = round((column + 1) * image.width / columns)
    bottom = round((row + 1) * image.height / rows)
    return image.crop((left, top, max(left + 1, right), max(top + 1, bottom)))


def build_region_confidence_map(
    source_paths: list[Path],
    preflights: list[PreflightResult],
    fusion: MultiReferenceFusionEvidence,
    *,
    rows: int = GRID_ROWS,
    columns: int = GRID_COLUMNS,
) -> RegionConfidenceMapEvidence:
    if not source_paths or len(source_paths) != len(preflights):
        raise ValueError(
            "region evidence requires matching source paths and preflight results"
        )
    if fusion.reference_count != len(source_paths):
        raise ValueError("reference fusion count does not match region evidence inputs")
    if not 0 <= fusion.primary_index < len(source_paths):
        raise ValueError("reference fusion primary index is out of range")
    if rows < 1 or columns < 1:
        raise ValueError("region evidence grid dimensions must be positive")

    primary_index = fusion.primary_index
    primary_preflight = preflights[primary_index]
    primary_quality = reference_quality_score(primary_preflight)
    primary_image = _canonical_crop(
        source_paths[primary_index],
        primary_preflight,
    )

    supporting_indices: list[int] = []
    excluded_indices: list[int] = []
    seen_hashes = {primary_preflight.sha256}
    canonical_support: dict[int, Image.Image] = {}

    for index in range(len(source_paths)):
        if index == primary_index:
            continue
        preflight = preflights[index]
        if index not in fusion.consistent_indices:
            excluded_indices.append(index)
            continue
        if preflight.sha256 in seen_hashes:
            excluded_indices.append(index)
            continue
        if (
            _aspect_compatibility(primary_preflight, preflight)
            < ASPECT_COMPATIBILITY_MIN
        ):
            excluded_indices.append(index)
            continue
        try:
            canonical_support[index] = _canonical_crop(
                source_paths[index],
                preflight,
            )
        except (OSError, ValueError):
            excluded_indices.append(index)
            continue
        seen_hashes.add(preflight.sha256)
        supporting_indices.append(index)

    cells: list[RegionEvidenceCell] = []
    supported_cells = 0
    low_confidence_cells = 0

    for row in range(rows):
        for column in range(columns):
            primary_block = _cell_crop(
                primary_image,
                row,
                column,
                rows,
                columns,
            )
            similarities: list[tuple[float, float]] = []
            local_support = 1

            for index in supporting_indices:
                support_block = _cell_crop(
                    canonical_support[index],
                    row,
                    column,
                    rows,
                    columns,
                )
                similarity = _block_similarity(primary_block, support_block)
                quality = reference_quality_score(preflights[index])
                similarities.append((similarity, quality))
                if similarity >= REGION_SUPPORT_SIMILARITY:
                    local_support += 1

            reasons: list[str] = []
            if similarities:
                agreement = sum(item[0] for item in similarities) / len(
                    similarities
                )
                quality_weight = sum(item[1] for item in similarities)
                if quality_weight > 0:
                    weighted_agreement = sum(
                        similarity * quality
                        for similarity, quality in similarities
                    ) / quality_weight
                else:
                    weighted_agreement = agreement

                independent_support = local_support - 1
                support_fraction = independent_support / max(
                    1,
                    len(supporting_indices),
                )
                confidence = (
                    0.45 * primary_quality
                    + 0.45 * weighted_agreement
                    + 0.10 * support_fraction
                )
                if independent_support > 0:
                    supported_cells += 1
                else:
                    reasons.append("no_local_consensus")
                if agreement < REGION_SUPPORT_SIMILARITY:
                    reasons.append("local_disagreement")
            else:
                agreement = 0.0
                confidence = 0.55 * primary_quality
                reasons.append("primary_only")

            confidence = max(0.0, min(1.0, confidence))
            if confidence < LOW_CONFIDENCE_THRESHOLD:
                low_confidence_cells += 1
                reasons.append("low_confidence")

            cells.append(
                RegionEvidenceCell(
                    row=row,
                    column=column,
                    bbox=BoundingBox(
                        x=column / columns,
                        y=row / rows,
                        width=1.0 / columns,
                        height=1.0 / rows,
                    ),
                    confidence=confidence,
                    agreement=max(0.0, min(1.0, agreement)),
                    support_count=local_support,
                    comparison_count=len(similarities),
                    reason_codes=sorted(set(reasons)),
                )
            )

    confidences = [cell.confidence for cell in cells]
    total_cells = max(1, len(cells))
    reason_codes: list[str] = []
    if not supporting_indices:
        reason_codes.append("no_independent_compatible_support")
    if excluded_indices:
        reason_codes.append("references_excluded_from_region_support")
    if fusion.conflict_detected:
        reason_codes.append("global_reference_conflict_present")

    return RegionConfidenceMapEvidence(
        rows=rows,
        columns=columns,
        primary_index=primary_index,
        supporting_indices=sorted(supporting_indices),
        excluded_indices=sorted(set(excluded_indices)),
        mean_confidence=sum(confidences) / total_cells,
        minimum_confidence=min(confidences) if confidences else 0.0,
        support_coverage=supported_cells / total_cells,
        low_confidence_cells=low_confidence_cells,
        cells=cells,
        reason_codes=reason_codes,
    )
