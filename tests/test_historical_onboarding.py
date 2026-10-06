from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from pod_artwork_engine.dataset_registry import DatasetRegistry
from pod_artwork_engine.historical_import import HistoricalImporter
from pod_artwork_engine.historical_onboarding import (
    HistoricalOnboardingBuilder,
    HistoricalOnboardingStatus,
)


def _save(
    path: Path,
    color: tuple[int, int, int],
    *,
    size: tuple[int, int] = (96, 96),
) -> Path:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((14, 14, 82, 82), fill=color)
    draw.ellipse((30, 30, 66, 66), fill=tuple(reversed(color)))
    image.save(path)
    return path


def _paired_folders(tmp_path: Path, count: int = 3) -> tuple[Path, Path]:
    sources = tmp_path / "sources"
    targets = tmp_path / "targets"
    sources.mkdir()
    targets.mkdir()
    for index in range(count):
        _save(
            sources / f"design-{index:03d}-front.png",
            (20 + index * 15, 60 + index * 10, 120 + index * 5),
        )
        _save(
            targets / f"design-{index:03d}-final.png",
            (180 - index * 10, 40 + index * 20, 70 + index * 10),
            size=(96 + index * 10, 96),
        )
    return sources, targets


def test_clean_folder_corpus_is_ready_and_projects_golden_split(
    tmp_path: Path,
) -> None:
    sources, targets = _paired_folders(tmp_path)

    report = HistoricalOnboardingBuilder().build_folders(
        sources,
        targets,
        allow_visual_fallback=False,
        minimum_golden_cases=1,
    )

    assert report.status is HistoricalOnboardingStatus.READY_TO_IMPORT
    assert report.ready_to_import is True
    assert report.pair_count == 3
    assert report.projected_golden_case_count >= 1
    assert sum(report.projected_pair_split_counts.values()) == 3
    assert report.invalid_images == []
    assert report.unmatched_sources == []
    assert report.unmatched_targets == []
    assert report.mutates_registry is False
    assert report.production_execution_enabled is False


def test_unmatched_source_and_target_block_import_readiness(tmp_path: Path) -> None:
    sources, targets = _paired_folders(tmp_path, count=3)
    _save(sources / "orphan-a-front.png", (1, 2, 3))
    _save(targets / "orphan-b-final.png", (4, 5, 6))

    report = HistoricalOnboardingBuilder().build_folders(
        sources,
        targets,
        allow_visual_fallback=False,
        minimum_golden_cases=1,
    )

    assert report.status is HistoricalOnboardingStatus.BLOCKED
    assert report.unmatched_sources
    assert report.unmatched_targets
    assert "historical_onboarding_unmatched_sources" in report.blockers
    assert "historical_onboarding_unmatched_targets" in report.blockers


def test_invalid_image_and_target_conflict_are_detected(tmp_path: Path) -> None:
    sources, targets = _paired_folders(tmp_path, count=3)
    (sources / "broken-front.png").write_bytes(b"not-an-image")
    _save(targets / "conflict-final.png", (10, 20, 30))
    _save(targets / "conflict-target.jpg", (200, 10, 40))
    _save(sources / "conflict-front.png", (40, 80, 120))

    report = HistoricalOnboardingBuilder().build_folders(
        sources,
        targets,
        allow_visual_fallback=False,
        minimum_golden_cases=1,
    )

    assert report.status is HistoricalOnboardingStatus.BLOCKED
    assert report.invalid_images
    assert report.conflicts
    assert "historical_onboarding_invalid_images" in report.blockers
    assert "historical_onboarding_pair_conflicts" in report.blockers


def test_equivalent_target_duplicates_are_warned_not_silently_ignored(
    tmp_path: Path,
) -> None:
    sources, targets = _paired_folders(tmp_path, count=3)
    original = targets / "design-000-final.png"
    duplicate = targets / "design-000-target.png"
    duplicate.write_bytes(original.read_bytes())

    report = HistoricalOnboardingBuilder().build_folders(
        sources,
        targets,
        allow_visual_fallback=False,
        minimum_golden_cases=1,
    )

    assert report.status is HistoricalOnboardingStatus.READY_TO_IMPORT
    assert str(duplicate) in report.target_duplicates
    assert report.exact_duplicate_targets
    assert "historical_onboarding_equivalent_target_duplicates" in report.warnings
    assert "historical_onboarding_exact_duplicate_targets" in report.warnings


def test_manifest_preflight_is_read_only_and_validates_explicit_pairs(
    tmp_path: Path,
) -> None:
    sources, targets = _paired_folders(tmp_path, count=3)
    pairs = []
    for index in range(3):
        pairs.append(
            {
                "design_id": f"SKU-{index}",
                "target": str(
                    (targets / f"design-{index:03d}-final.png").relative_to(tmp_path)
                ),
                "sources": [
                    str(
                        (sources / f"design-{index:03d}-front.png").relative_to(tmp_path)
                    )
                ],
            }
        )
    manifest = tmp_path / "pairs.json"
    manifest.write_text(json.dumps({"pairs": pairs}), encoding="utf-8")

    report = HistoricalOnboardingBuilder().build_manifest(
        manifest,
        minimum_golden_cases=1,
    )

    assert report.status is HistoricalOnboardingStatus.READY_TO_IMPORT
    assert report.mode.value == "manifest"
    assert report.pair_count == 3
    assert {pair.pair_key for pair in report.pairs} == {"SKU-0", "SKU-1", "SKU-2"}
    assert report.mutates_registry is False
    assert report.production_execution_enabled is False


def test_projected_split_matches_registry_split_after_explicit_import(
    tmp_path: Path,
) -> None:
    sources, targets = _paired_folders(tmp_path, count=8)
    seed = "projection-parity"

    report = HistoricalOnboardingBuilder().build_folders(
        sources,
        targets,
        allow_visual_fallback=False,
        seed=seed,
        minimum_golden_cases=1,
    )
    assert report.ready_to_import is True

    registry = DatasetRegistry(tmp_path / "engine.sqlite3", tmp_path / "datasets")
    imported = HistoricalImporter(registry).import_folders(
        sources,
        targets,
        dataset_name="projection-parity",
        seed=seed,
        allow_visual_fallback=False,
    )

    assert imported.dataset is not None
    assert report.projected_pair_split_counts == imported.dataset.split_counts


def test_small_corpus_blocks_when_projected_golden_evidence_is_too_small(
    tmp_path: Path,
) -> None:
    sources, targets = _paired_folders(tmp_path, count=3)

    report = HistoricalOnboardingBuilder().build_folders(
        sources,
        targets,
        allow_visual_fallback=False,
        minimum_golden_cases=3,
    )

    assert report.status is HistoricalOnboardingStatus.BLOCKED
    assert report.projected_golden_case_count < 3
    assert (
        "historical_onboarding_insufficient_projected_golden_cases"
        in report.blockers
    )
