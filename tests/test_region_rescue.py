from __future__ import annotations

from pod_artwork_engine.contracts import (
    BoundingBox,
    MultiReferenceFusionEvidence,
    RegionConfidenceMapEvidence,
    RegionEvidenceCell,
    RegionRescueDisposition,
)
from pod_artwork_engine.region_rescue import build_region_rescue_plan


def _cell(
    row: int,
    column: int,
    confidence: float,
    *,
    reasons: list[str] | None = None,
) -> RegionEvidenceCell:
    return RegionEvidenceCell(
        row=row,
        column=column,
        bbox=BoundingBox(
            x=column / 4,
            y=row / 4,
            width=0.25,
            height=0.25,
        ),
        confidence=confidence,
        agreement=confidence,
        support_count=2 if confidence >= 0.60 else 1,
        comparison_count=1,
        reason_codes=list(reasons or []),
    )


def _region_map(cells: list[RegionEvidenceCell]) -> RegionConfidenceMapEvidence:
    confidences = [cell.confidence for cell in cells]
    low = sum(cell.confidence < 0.60 for cell in cells)
    return RegionConfidenceMapEvidence(
        rows=4,
        columns=4,
        primary_index=0,
        supporting_indices=[1],
        mean_confidence=sum(confidences) / max(1, len(confidences)),
        minimum_confidence=min(confidences) if confidences else 0.0,
        support_coverage=1.0 if cells else 0.0,
        low_confidence_cells=low,
        cells=cells,
    )


def _fusion(*, conflict: bool = False) -> MultiReferenceFusionEvidence:
    return MultiReferenceFusionEvidence(
        reference_count=2,
        primary_index=0,
        consensus_confidence=0.86 if not conflict else 0.40,
        conflict_detected=conflict,
        consistent_indices=[0, 1] if not conflict else [0],
        conflicting_indices=[1] if conflict else [],
    )


def test_rescue_plan_is_noop_when_all_regions_are_confident() -> None:
    cells = [
        _cell(row, column, 0.90)
        for row in range(4)
        for column in range(4)
    ]

    plan = build_region_rescue_plan(
        _region_map(cells),
        _fusion(),
        provider_available=True,
    )

    assert plan.disposition is RegionRescueDisposition.NONE
    assert plan.fail_closed is False
    assert plan.targets == []
    assert plan.target_cell_count == 0
    assert plan.reason_codes == ["no_low_confidence_regions"]


def test_rescue_plan_merges_only_rectangular_adjacent_low_cells() -> None:
    low = {(0, 0), (0, 1), (1, 0), (1, 1)}
    cells = [
        _cell(
            row,
            column,
            0.42 if (row, column) in low else 0.90,
            reasons=["low_confidence"] if (row, column) in low else [],
        )
        for row in range(4)
        for column in range(4)
    ]

    plan = build_region_rescue_plan(
        _region_map(cells),
        _fusion(),
        provider_available=True,
    )

    assert plan.disposition is RegionRescueDisposition.SEMANTIC_PROVIDER_CANDIDATE
    assert plan.fail_closed is False
    assert plan.target_cell_count == 4
    assert len(plan.targets) == 1
    target = plan.targets[0]
    assert target.bbox == BoundingBox(x=0, y=0, width=0.5, height=0.5)
    assert [(cell.row, cell.column) for cell in target.cells] == [
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
    ]
    assert target.required_capabilities == ["need_region_rescue"]


def test_rescue_plan_merges_multiple_vertical_spans_independently() -> None:
    low = {
        (0, 0),
        (0, 1),
        (0, 3),
        (1, 0),
        (1, 1),
        (1, 3),
    }
    cells = [
        _cell(
            row,
            column,
            0.41 if (row, column) in low else 0.93,
            reasons=["low_confidence"] if (row, column) in low else [],
        )
        for row in range(4)
        for column in range(4)
    ]

    plan = build_region_rescue_plan(
        _region_map(cells),
        _fusion(),
        provider_available=True,
    )

    assert plan.target_cell_count == 6
    assert len(plan.targets) == 2
    assert plan.targets[0].bbox == BoundingBox(
        x=0,
        y=0,
        width=0.5,
        height=0.5,
    )
    assert plan.targets[1].bbox == BoundingBox(
        x=0.75,
        y=0,
        width=0.25,
        height=0.5,
    )


def test_rescue_plan_does_not_expand_irregular_shape_over_good_cell() -> None:
    low = {(0, 0), (0, 1), (1, 0)}
    cells = [
        _cell(
            row,
            column,
            0.40 if (row, column) in low else 0.92,
            reasons=["low_confidence"] if (row, column) in low else [],
        )
        for row in range(4)
        for column in range(4)
    ]

    plan = build_region_rescue_plan(
        _region_map(cells),
        _fusion(),
        provider_available=True,
    )

    assert plan.target_cell_count == 3
    assert len(plan.targets) == 2
    target_cells = {
        (cell.row, cell.column)
        for target in plan.targets
        for cell in target.cells
    }
    assert target_cells == low
    assert (1, 1) not in target_cells


def test_rescue_plan_fails_closed_on_global_reference_conflict() -> None:
    cells = [_cell(0, 0, 0.35, reasons=["low_confidence"])]

    plan = build_region_rescue_plan(
        _region_map(cells),
        _fusion(conflict=True),
        provider_available=True,
    )

    assert plan.disposition is RegionRescueDisposition.MANUAL_REVIEW
    assert plan.fail_closed is True
    assert plan.targets == []
    assert "global_reference_conflict" in plan.reason_codes
    assert "need_reference_disambiguation" in plan.required_capabilities


def test_rescue_plan_fails_closed_when_region_evidence_is_unavailable() -> None:
    region_map = RegionConfidenceMapEvidence(
        primary_index=0,
        mean_confidence=0,
        minimum_confidence=0,
        support_coverage=0,
        low_confidence_cells=0,
        reason_codes=["region_evidence_unavailable"],
    )

    plan = build_region_rescue_plan(
        region_map,
        _fusion(),
        provider_available=True,
    )

    assert plan.disposition is RegionRescueDisposition.MANUAL_REVIEW
    assert plan.fail_closed is True
    assert plan.targets == []
    assert plan.required_capabilities == ["need_manual_review"]


def test_rescue_plan_fails_closed_on_incomplete_region_grid() -> None:
    region_map = RegionConfidenceMapEvidence(
        rows=4,
        columns=4,
        primary_index=0,
        supporting_indices=[1],
        mean_confidence=0.90,
        minimum_confidence=0.90,
        support_coverage=1.0,
        low_confidence_cells=0,
        cells=[_cell(0, 0, 0.90)],
    )

    plan = build_region_rescue_plan(
        region_map,
        _fusion(),
        provider_available=True,
    )

    assert plan.disposition is RegionRescueDisposition.MANUAL_REVIEW
    assert plan.fail_closed is True
    assert plan.reason_codes == ["region_evidence_incomplete"]


def test_rescue_plan_keeps_targets_but_requires_review_without_provider() -> None:
    cells = [
        _cell(
            row,
            column,
            0.38 if (row, column) == (0, 0) else 0.91,
            reasons=["low_confidence"] if (row, column) == (0, 0) else [],
        )
        for row in range(4)
        for column in range(4)
    ]

    plan = build_region_rescue_plan(
        _region_map(cells),
        _fusion(),
        provider_available=False,
    )

    assert plan.disposition is RegionRescueDisposition.MANUAL_REVIEW
    assert plan.fail_closed is True
    assert plan.target_cell_count == 1
    assert len(plan.targets) == 1
    assert plan.required_capabilities == [
        "need_manual_review",
        "need_region_rescue",
    ]
