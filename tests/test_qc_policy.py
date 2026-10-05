from __future__ import annotations

from pathlib import Path

from PIL import Image

from pod_artwork_engine.contracts import (
    ArtworkType,
    BoundingBox,
    DesignSpec,
    QualityMode,
)
from pod_artwork_engine.qc import semantic_qc, technical_qc
from pod_artwork_engine.qc_policy import QCModePolicy, QCPolicy, load_qc_policy
from pod_artwork_engine.reconstruction import CandidateInfo


def _strict_policy() -> QCPolicy:
    return QCPolicy(
        policy_id="strict-test",
        version="1",
        print_ready=QCModePolicy(
            semantic_min_score=0.90,
            object_fidelity_min=0.80,
            required_native_long_edge=1000,
            min_resolution_score=0.90,
        ),
    )


def test_semantic_qc_uses_explicit_policy_threshold() -> None:
    spec = DesignSpec(
        artwork_type=ArtworkType.LOGO,
        artwork_bbox=BoundingBox(x=0.1, y=0.1, width=0.8, height=0.8),
        confidence=0.80,
    )

    default_result = semantic_qc(spec, QualityMode.PRINT_READY)
    strict_result = semantic_qc(
        spec,
        QualityMode.PRINT_READY,
        policy=_strict_policy(),
    )

    assert default_result.passed is True
    assert strict_result.passed is False
    assert strict_result.metrics["policy_id"] == "strict-test"
    assert strict_result.metrics["semantic_threshold"] == 0.9


def test_technical_qc_uses_explicit_resolution_policy(tmp_path: Path) -> None:
    output = tmp_path / "output.png"
    Image.new("RGBA", (4500, 5400), (0, 0, 0, 0)).save(
        output,
        format="PNG",
        dpi=(300, 300),
    )
    candidate = CandidateInfo(
        path=output,
        source_path=output,
        native_width=850,
        native_height=850,
        alpha_method="fixture",
        local_baseline=True,
    )

    result = technical_qc(
        output,
        candidate,
        QualityMode.PRINT_READY,
        policy=_strict_policy(),
    )

    assert result.passed is False
    assert result.metrics["required_native_long_edge"] == 1000
    assert result.metrics["min_resolution_score"] == 0.9
    assert result.metrics["resolution_score"] == 0.85


def test_qc_policy_file_is_strictly_loaded(tmp_path: Path) -> None:
    path = tmp_path / "qc-policy.json"
    policy = _strict_policy()
    path.write_text(policy.model_dump_json(indent=2), encoding="utf-8")

    loaded = load_qc_policy(path)

    assert loaded.policy_id == "strict-test"
    assert loaded.print_ready.semantic_min_score == 0.90
