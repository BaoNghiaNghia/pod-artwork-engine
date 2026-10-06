from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, ImageStat

from .artwork_detection import crop_artwork
from .contracts import (
    MultiReferenceFusionEvidence,
    PreflightResult,
    ReferenceEvidence,
)


CONSISTENT_SIMILARITY = 0.62
CONFLICT_SIMILARITY = 0.42
HIGH_QUALITY_EVIDENCE = 0.55


@dataclass(frozen=True)
class _ReferenceSignature:
    mean_rgb: tuple[float, float, float]
    difference_hash: int
    aspect_ratio: float


def reference_quality_score(preflight: PreflightResult) -> float:
    resolution_score = min(
        1.0,
        math.log2(max(preflight.width, preflight.height, 256) / 256) / 4.0,
    )
    score = (
        0.52 * preflight.artwork_confidence
        + 0.38 * preflight.source_quality
        + 0.10 * max(0.0, resolution_score)
    )
    return max(0.0, min(1.0, score))


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


def _signature(path: Path, preflight: PreflightResult) -> _ReferenceSignature:
    if preflight.artwork_bbox is None:
        raise ValueError("artwork bounding box is unavailable for reference fusion")

    crop = crop_artwork(path, preflight.artwork_bbox).convert("RGB")
    working = crop.copy()
    working.thumbnail((384, 384), Image.Resampling.LANCZOS)
    stat = ImageStat.Stat(working)
    mean = tuple(float(value) for value in stat.mean[:3])
    return _ReferenceSignature(
        mean_rgb=(mean[0], mean[1], mean[2]),
        difference_hash=_difference_hash(working),
        aspect_ratio=crop.width / max(1, crop.height),
    )


def _similarity(
    first: _ReferenceSignature,
    second: _ReferenceSignature,
) -> float:
    hamming = (first.difference_hash ^ second.difference_hash).bit_count()
    shape_similarity = 1.0 - (hamming / 64.0)

    color_distance = math.sqrt(
        sum(
            (first.mean_rgb[channel] - second.mean_rgb[channel]) ** 2
            for channel in range(3)
        )
    )
    color_similarity = 1.0 - min(
        1.0,
        color_distance / (math.sqrt(3.0) * 255.0),
    )

    minimum_aspect = min(first.aspect_ratio, second.aspect_ratio)
    maximum_aspect = max(first.aspect_ratio, second.aspect_ratio)
    aspect_similarity = minimum_aspect / max(maximum_aspect, 1e-6)

    similarity = (
        0.55 * shape_similarity
        + 0.25 * color_similarity
        + 0.20 * aspect_similarity
    )
    return max(0.0, min(1.0, similarity))


def build_reference_fusion(
    source_paths: list[Path],
    preflights: list[PreflightResult],
) -> MultiReferenceFusionEvidence:
    if not source_paths or len(source_paths) != len(preflights):
        raise ValueError(
            "reference fusion requires matching source paths and preflight results"
        )

    qualities = [reference_quality_score(item) for item in preflights]
    primary_index = max(
        range(len(preflights)),
        key=lambda index: (
            qualities[index],
            preflights[index].artwork_confidence,
            preflights[index].source_quality,
            preflights[index].width * preflights[index].height,
            -index,
        ),
    )

    if len(source_paths) == 1:
        return MultiReferenceFusionEvidence(
            reference_count=1,
            primary_index=0,
            consensus_confidence=qualities[0],
            consistent_indices=[0],
            references=[
                ReferenceEvidence(
                    index=0,
                    sha256=preflights[0].sha256,
                    quality_score=qualities[0],
                    similarity_to_primary=1.0,
                    status="primary",
                    reason_codes=["single_reference"],
                )
            ],
        )

    signatures: list[_ReferenceSignature | None] = []
    for path, preflight in zip(source_paths, preflights, strict=True):
        try:
            signatures.append(_signature(path, preflight))
        except (OSError, ValueError):
            signatures.append(None)

    primary_signature = signatures[primary_index]
    references: list[ReferenceEvidence] = []
    consistent_indices = [primary_index]
    ambiguous_indices: list[int] = []
    conflicting_indices: list[int] = []
    similarity_values: list[float] = []

    for index, preflight in enumerate(preflights):
        if index == primary_index:
            references.append(
                ReferenceEvidence(
                    index=index,
                    sha256=preflight.sha256,
                    quality_score=qualities[index],
                    similarity_to_primary=1.0,
                    status="primary",
                    reason_codes=["highest_ranked_evidence"],
                )
            )
            continue

        signature = signatures[index]
        if primary_signature is None or signature is None:
            similarity = 0.5
            status = "ambiguous"
            reason_codes = ["visual_signature_unavailable"]
            ambiguous_indices.append(index)
        else:
            similarity = _similarity(primary_signature, signature)
            similarity_values.append(similarity)
            if similarity >= CONSISTENT_SIMILARITY:
                status = "consistent"
                reason_codes = ["visual_consensus"]
                consistent_indices.append(index)
            elif (
                similarity < CONFLICT_SIMILARITY
                and qualities[index] >= HIGH_QUALITY_EVIDENCE
                and qualities[primary_index] >= HIGH_QUALITY_EVIDENCE
            ):
                status = "conflicting"
                reason_codes = ["high_quality_visual_conflict"]
                conflicting_indices.append(index)
            else:
                status = "ambiguous"
                reason_codes = ["visual_similarity_inconclusive"]
                ambiguous_indices.append(index)

        references.append(
            ReferenceEvidence(
                index=index,
                sha256=preflight.sha256,
                quality_score=qualities[index],
                similarity_to_primary=similarity,
                status=status,
                reason_codes=reason_codes,
            )
        )

    conflict_detected = bool(conflicting_indices)
    supporting_qualities = [qualities[index] for index in consistent_indices]
    support_score = sum(supporting_qualities) / max(1, len(supporting_qualities))
    if similarity_values:
        mean_similarity = sum(similarity_values) / len(similarity_values)
    else:
        mean_similarity = 1.0

    consensus_confidence = 0.65 * support_score + 0.35 * mean_similarity
    if conflict_detected:
        consensus_confidence *= 0.55
    elif ambiguous_indices:
        consensus_confidence *= 0.85

    return MultiReferenceFusionEvidence(
        reference_count=len(source_paths),
        primary_index=primary_index,
        consensus_confidence=max(0.0, min(1.0, consensus_confidence)),
        conflict_detected=conflict_detected,
        consistent_indices=sorted(consistent_indices),
        ambiguous_indices=sorted(ambiguous_indices),
        conflicting_indices=sorted(conflicting_indices),
        references=sorted(references, key=lambda item: item.index),
    )
