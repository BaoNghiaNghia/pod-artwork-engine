from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    BoundingBox,
    MultiReferenceFusionEvidence,
    RegionCellRef,
    RegionConfidenceMapEvidence,
    RegionEvidenceCell,
    RegionRescueDisposition,
    RegionRescuePlanEvidence,
    RegionRescueTarget,
)


LOW_CONFIDENCE_THRESHOLD = 0.60


@dataclass(frozen=True)
class _CellRun:
    row_start: int
    row_end: int
    column_start: int
    column_end: int
    cells: tuple[RegionEvidenceCell, ...]


def _is_low_confidence(cell: RegionEvidenceCell) -> bool:
    return (
        cell.confidence < LOW_CONFIDENCE_THRESHOLD
        or "low_confidence" in cell.reason_codes
        or "local_disagreement" in cell.reason_codes
        or "no_local_consensus" in cell.reason_codes
    )


def _horizontal_runs(
    cells: list[RegionEvidenceCell],
    *,
    rows: int,
    columns: int,
) -> list[_CellRun]:
    lookup = {(cell.row, cell.column): cell for cell in cells if _is_low_confidence(cell)}
    runs: list[_CellRun] = []
    for row in range(rows):
        column = 0
        while column < columns:
            if (row, column) not in lookup:
                column += 1
                continue
            start = column
            members: list[RegionEvidenceCell] = []
            while column < columns and (row, column) in lookup:
                members.append(lookup[(row, column)])
                column += 1
            runs.append(
                _CellRun(
                    row_start=row,
                    row_end=row,
                    column_start=start,
                    column_end=column - 1,
                    cells=tuple(members),
                )
            )
    return runs


def _merge_vertical_runs(runs: list[_CellRun]) -> list[_CellRun]:
    merged: list[_CellRun] = []
    latest_by_span: dict[tuple[int, int], int] = {}

    for run in runs:
        span = (run.column_start, run.column_end)
        previous_index = latest_by_span.get(span)
        if (
            previous_index is not None
            and merged[previous_index].row_end + 1 == run.row_start
        ):
            previous = merged[previous_index]
            merged[previous_index] = _CellRun(
                row_start=previous.row_start,
                row_end=run.row_end,
                column_start=run.column_start,
                column_end=run.column_end,
                cells=previous.cells + run.cells,
            )
        else:
            merged.append(run)
            previous_index = len(merged) - 1

        latest_by_span[span] = previous_index

    return sorted(
        merged,
        key=lambda item: (
            item.row_start,
            item.column_start,
            item.row_end,
            item.column_end,
        ),
    )


def _target_from_run(
    run: _CellRun,
    *,
    rows: int,
    columns: int,
) -> RegionRescueTarget:
    confidences = [cell.confidence for cell in run.cells]
    reasons = sorted(
        {
            reason
            for cell in run.cells
            for reason in cell.reason_codes
            if reason
        }
    )
    if not reasons:
        reasons = ["low_region_confidence"]

    return RegionRescueTarget(
        bbox=BoundingBox(
            x=run.column_start / columns,
            y=run.row_start / rows,
            width=(run.column_end - run.column_start + 1) / columns,
            height=(run.row_end - run.row_start + 1) / rows,
        ),
        cells=[
            RegionCellRef(row=cell.row, column=cell.column)
            for cell in sorted(run.cells, key=lambda item: (item.row, item.column))
        ],
        mean_confidence=sum(confidences) / max(1, len(confidences)),
        maximum_confidence=max(confidences) if confidences else 0.0,
        reason_codes=reasons,
        required_capabilities=["need_region_rescue"],
    )


def build_region_rescue_plan(
    region_map: RegionConfidenceMapEvidence,
    fusion: MultiReferenceFusionEvidence,
    *,
    provider_available: bool,
) -> RegionRescuePlanEvidence:
    if region_map.primary_index != fusion.primary_index:
        raise ValueError("region map primary index does not match reference fusion")

    if fusion.conflict_detected:
        return RegionRescuePlanEvidence(
            disposition=RegionRescueDisposition.MANUAL_REVIEW,
            primary_index=fusion.primary_index,
            provider_available=provider_available,
            fail_closed=True,
            required_capabilities=[
                "need_manual_review",
                "need_reference_disambiguation",
            ],
            reason_codes=["global_reference_conflict"],
        )

    unavailable = "region_evidence_unavailable" in region_map.reason_codes
    expected_coordinates = {
        (row, column)
        for row in range(region_map.rows)
        for column in range(region_map.columns)
    }
    actual_coordinates = {
        (cell.row, cell.column)
        for cell in region_map.cells
    }
    incomplete = actual_coordinates != expected_coordinates
    if unavailable or incomplete:
        reason = (
            "region_evidence_unavailable"
            if unavailable
            else "region_evidence_incomplete"
        )
        return RegionRescuePlanEvidence(
            disposition=RegionRescueDisposition.MANUAL_REVIEW,
            primary_index=fusion.primary_index,
            provider_available=provider_available,
            fail_closed=True,
            required_capabilities=["need_manual_review"],
            reason_codes=[reason],
        )

    low_cells = [cell for cell in region_map.cells if _is_low_confidence(cell)]
    if not low_cells:
        return RegionRescuePlanEvidence(
            disposition=RegionRescueDisposition.NONE,
            primary_index=fusion.primary_index,
            provider_available=provider_available,
            fail_closed=False,
            reason_codes=["no_low_confidence_regions"],
        )

    runs = _merge_vertical_runs(
        _horizontal_runs(
            low_cells,
            rows=region_map.rows,
            columns=region_map.columns,
        )
    )
    targets = [
        _target_from_run(
            run,
            rows=region_map.rows,
            columns=region_map.columns,
        )
        for run in runs
    ]

    if provider_available:
        disposition = RegionRescueDisposition.SEMANTIC_PROVIDER_CANDIDATE
        required_capabilities = [
            "need_region_rescue",
            "need_semantic_reconstruction",
        ]
        reason_codes = ["low_confidence_regions_semantic_candidate"]
    else:
        disposition = RegionRescueDisposition.MANUAL_REVIEW
        required_capabilities = [
            "need_manual_review",
            "need_region_rescue",
        ]
        reason_codes = ["low_confidence_regions_provider_unavailable"]

    target_cell_count = sum(len(target.cells) for target in targets)
    return RegionRescuePlanEvidence(
        disposition=disposition,
        primary_index=fusion.primary_index,
        provider_available=provider_available,
        fail_closed=disposition is RegionRescueDisposition.MANUAL_REVIEW,
        target_cell_count=target_cell_count,
        targets=targets,
        required_capabilities=required_capabilities,
        reason_codes=reason_codes,
    )
