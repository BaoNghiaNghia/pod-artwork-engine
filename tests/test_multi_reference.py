from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from pod_artwork_engine.contracts import (
    BoundingBox,
    DesignSpec,
    MultiReferenceFusionEvidence,
    JobState,
    PreflightResult,
    QualityMode,
    RouteKind,
)
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.multi_reference import build_reference_fusion
from pod_artwork_engine.reconstruction import reconstruct_local_baseline
from pod_artwork_engine.router import choose_route
from pod_artwork_engine.settings import Settings


def _gradient(
    path: Path,
    *,
    start: int,
    end: int,
    size: tuple[int, int] = (320, 220),
) -> Path:
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            amount = x / max(1, image.width - 1)
            value = round(start * (1.0 - amount) + end * amount)
            pixels[x, y] = (value, value, value)
    image.save(path)
    return path


def _preflight(
    path: Path,
    *,
    artwork_confidence: float = 0.90,
    source_quality: float = 0.90,
) -> PreflightResult:
    with Image.open(path) as image:
        width, height = image.size
    return PreflightResult(
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        filename=path.name,
        width=width,
        height=height,
        image_mode="RGB",
        image_format="PNG",
        file_size_bytes=path.stat().st_size,
        source_quality=source_quality,
        artwork_bbox=BoundingBox(x=0, y=0, width=1, height=1),
        artwork_confidence=artwork_confidence,
    )


def test_reference_fusion_groups_visually_consistent_references(
    tmp_path: Path,
) -> None:
    first = _gradient(tmp_path / "first.png", start=20, end=180)
    second = _gradient(
        tmp_path / "second.png",
        start=25,
        end=185,
        size=(640, 440),
    )
    preflights = [
        _preflight(first, artwork_confidence=0.88, source_quality=0.86),
        _preflight(second, artwork_confidence=0.94, source_quality=0.95),
    ]

    fusion = build_reference_fusion([first, second], preflights)

    assert fusion.reference_count == 2
    assert fusion.primary_index == 1
    assert fusion.conflict_detected is False
    assert fusion.consistent_indices == [0, 1]
    assert fusion.conflicting_indices == []
    assert fusion.consensus_confidence > 0.75
    assert fusion.references[0].status == "consistent"
    assert fusion.references[1].status == "primary"


def test_reference_fusion_flags_high_quality_visual_conflict(
    tmp_path: Path,
) -> None:
    first = _gradient(tmp_path / "first.png", start=0, end=100)
    second = _gradient(tmp_path / "second.png", start=255, end=155)
    preflights = [_preflight(first), _preflight(second)]

    fusion = build_reference_fusion([first, second], preflights)

    assert fusion.primary_index == 0
    assert fusion.conflict_detected is True
    assert fusion.conflicting_indices == [1]
    assert fusion.references[1].status == "conflicting"
    assert fusion.consensus_confidence < 0.60


def test_local_baseline_uses_fusion_selected_primary_reference(
    tmp_path: Path,
) -> None:
    weak = _gradient(tmp_path / "weak.png", start=5, end=80)
    strong = _gradient(tmp_path / "strong.png", start=90, end=210)
    preflights = [
        _preflight(weak, artwork_confidence=0.50, source_quality=0.50),
        _preflight(strong, artwork_confidence=0.98, source_quality=0.98),
    ]
    fusion = build_reference_fusion([weak, strong], preflights)

    candidate = reconstruct_local_baseline(
        [weak, strong],
        preflights,
        DesignSpec(artwork_bbox=BoundingBox(x=0, y=0, width=1, height=1)),
        tmp_path / "candidate.png",
        primary_index=fusion.primary_index,
    )

    assert fusion.primary_index == 1
    assert candidate.source_path == strong
    assert candidate.path.is_file()


def test_engine_preflight_persists_reference_fusion_checkpoint(
    tmp_path: Path,
) -> None:
    first = _gradient(tmp_path / "first.png", start=20, end=180)
    second = _gradient(
        tmp_path / "second.png",
        start=25,
        end=185,
        size=(640, 440),
    )
    engine = Engine(Settings(data_root=tmp_path / "runtime"))
    job = engine.create_job([first, second], QualityMode.QUICK_2D)

    result = engine.run_preflight(job.job_id)

    assert result.state is JobState.WAITING_PROVIDER
    fusion = engine.checkpoints.payload(job.job_id, "reference_fusion")
    assert fusion["reference_count"] == 2
    assert fusion["conflict_detected"] is False
    assert len(fusion["references"]) == 2


def test_reference_conflict_forces_semantic_disambiguation() -> None:
    fusion = MultiReferenceFusionEvidence(
        reference_count=2,
        primary_index=0,
        consensus_confidence=0.41,
        conflict_detected=True,
        consistent_indices=[0],
        conflicting_indices=[1],
    )
    spec = DesignSpec(
        confidence=0.92,
        required_capabilities=["need_vector"],
    )

    constrained = Engine._apply_reference_fusion_constraints(spec, fusion)

    assert constrained.confidence == 0.41
    assert constrained.required_capabilities == [
        "need_reference_disambiguation",
        "need_semantic_reconstruction",
        "need_vector",
    ]

    route = choose_route(
        constrained,
        QualityMode.PRINT_READY,
        remote_available=True,
    )
    assert route.route is RouteKind.HYBRID
    assert route.use_remote_provider is True
    assert "reference_conflict_remote_disambiguation" in route.reason_codes


def test_single_reference_fusion_preserves_single_source_behavior(
    tmp_path: Path,
) -> None:
    source = _gradient(tmp_path / "single.png", start=20, end=180)
    preflight = _preflight(source)

    fusion = build_reference_fusion([source], [preflight])

    assert fusion.reference_count == 1
    assert fusion.primary_index == 0
    assert fusion.consistent_indices == [0]
    assert fusion.conflict_detected is False
    assert fusion.references[0].status == "primary"
