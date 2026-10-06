from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from pod_artwork_engine.contracts import (
    ArtworkType,
    BoundingBox,
    DesignSpec,
    FontMatchEvidence,
    GeometryKind,
    GeometryPrimitive,
    GeometrySpec,
    JobState,
    QualityMode,
    RepresentationComponentKind,
    RepresentationKind,
    TypographyLine,
    TypographySpec,
)
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.representation_plan import build_representation_plan
from pod_artwork_engine.settings import Settings


def _verified_typography() -> TypographySpec:
    return TypographySpec(
        lines=[
            TypographyLine(
                text="HELLO",
                bbox=BoundingBox(x=0.2, y=0.3, width=0.6, height=0.2),
                font_family="Verified Sans",
                font_weight=700,
                confidence=0.96,
                font_match=FontMatchEvidence(
                    family="Verified Sans",
                    style="Bold",
                    weight=700,
                    score=0.94,
                    margin=0.08,
                    accepted=True,
                    method="fixture",
                    candidates_evaluated=12,
                    font_sha256="a" * 64,
                ),
            )
        ],
        line_order_confidence=0.96,
        font_match_confidence=0.94,
        evidence_provider="fixture",
        evidence_version="1",
    )


def _verified_geometry() -> GeometrySpec:
    return GeometrySpec(
        primitives=[
            GeometryPrimitive(
                kind=GeometryKind.RECT,
                bbox=BoundingBox(x=0.2, y=0.2, width=0.6, height=0.6),
                fill="#111111",
                confidence=0.97,
            )
        ],
        confidence=0.96,
        evidence_provider="fixture",
        evidence_version="1",
    )


def test_verified_typography_is_vector_candidate() -> None:
    plan = build_representation_plan(
        DesignSpec(
            artwork_type=ArtworkType.TYPOGRAPHY,
            exact_text=["HELLO"],
            typography=_verified_typography(),
            confidence=0.95,
        )
    )

    assert plan.overall is RepresentationKind.VECTOR
    assert plan.fail_closed is False
    assert plan.vector_components == 1
    assert plan.raster_components == 0
    component = plan.components[0]
    assert component.component is RepresentationComponentKind.TYPOGRAPHY
    assert component.representation is RepresentationKind.VECTOR
    assert component.missing_capabilities == []


def test_unaccepted_font_match_keeps_typography_raster() -> None:
    typography = _verified_typography()
    line = typography.lines[0].model_copy(
        update={
            "font_match": typography.lines[0].font_match.model_copy(
                update={"accepted": False}
            )
        }
    )
    typography = typography.model_copy(update={"lines": [line]})

    plan = build_representation_plan(
        DesignSpec(
            artwork_type=ArtworkType.TYPOGRAPHY,
            exact_text=["HELLO"],
            typography=typography,
            confidence=0.93,
        )
    )

    assert plan.overall is RepresentationKind.RASTER
    assert plan.fail_closed is True
    assert "need_font_verification" in plan.missing_capabilities
    assert "font_match_not_accepted" in plan.components[0].reason_codes


def test_unverified_font_keeps_typography_raster() -> None:
    typography = _verified_typography().model_copy(
        update={"font_match_confidence": 0.42}
    )
    plan = build_representation_plan(
        DesignSpec(
            artwork_type=ArtworkType.TYPOGRAPHY,
            exact_text=["HELLO"],
            typography=typography,
            confidence=0.93,
        )
    )

    assert plan.overall is RepresentationKind.RASTER
    assert plan.fail_closed is True
    assert "need_font_verification" in plan.missing_capabilities
    assert plan.components[0].representation is RepresentationKind.RASTER


def test_verified_logo_geometry_is_vector_candidate() -> None:
    plan = build_representation_plan(
        DesignSpec(
            artwork_type=ArtworkType.LOGO,
            geometry=_verified_geometry(),
            confidence=0.94,
            required_capabilities=["need_vector"],
        )
    )

    assert plan.overall is RepresentationKind.VECTOR
    assert plan.vector_components == 1
    assert plan.fail_closed is False
    assert plan.components[0].component is RepresentationComponentKind.GEOMETRY


def test_logo_without_geometry_falls_back_to_raster_and_records_missing_capability() -> None:
    plan = build_representation_plan(
        DesignSpec(
            artwork_type=ArtworkType.LOGO,
            confidence=0.82,
            required_capabilities=["need_vector"],
        )
    )

    assert plan.overall is RepresentationKind.RASTER
    assert plan.fail_closed is True
    assert "need_vector_geometry" in plan.missing_capabilities


def test_illustration_stays_raster_without_false_vectorization() -> None:
    plan = build_representation_plan(
        DesignSpec(
            artwork_type=ArtworkType.ILLUSTRATION,
            objects=["dog"],
            texture_classes=["fine_detail"],
            confidence=0.89,
        )
    )

    assert plan.overall is RepresentationKind.RASTER
    assert plan.fail_closed is False
    assert plan.vector_components == 0
    assert plan.raster_components == 1
    component = plan.components[0]
    assert component.component is RepresentationComponentKind.ILLUSTRATION_TEXTURE
    assert component.representation is RepresentationKind.RASTER
    assert "fine_detail_raster_preservation" in component.reason_codes


def test_mixed_verified_text_and_illustration_produces_hybrid_plan() -> None:
    plan = build_representation_plan(
        DesignSpec(
            artwork_type=ArtworkType.MIXED,
            exact_text=["HELLO"],
            typography=_verified_typography(),
            objects=["flower"],
            texture_classes=["smooth_or_painterly"],
            confidence=0.91,
        )
    )

    assert plan.overall is RepresentationKind.HYBRID
    assert plan.fail_closed is False
    assert plan.vector_components == 1
    assert plan.raster_components == 1
    representations = {
        component.component: component.representation
        for component in plan.components
    }
    assert (
        representations[RepresentationComponentKind.TYPOGRAPHY]
        is RepresentationKind.VECTOR
    )
    assert (
        representations[RepresentationComponentKind.ILLUSTRATION_TEXTURE]
        is RepresentationKind.RASTER
    )


def test_unknown_content_is_preserved_as_raster_and_fails_closed() -> None:
    plan = build_representation_plan(
        DesignSpec(
            artwork_type=ArtworkType.UNKNOWN,
            confidence=0.40,
        )
    )

    assert plan.overall is RepresentationKind.RASTER
    assert plan.fail_closed is True
    assert plan.missing_capabilities == ["need_content_classification"]
    assert plan.components[0].component is RepresentationComponentKind.UNKNOWN_CONTENT


def test_engine_persists_representation_plan_without_changing_renderer(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    image = Image.new("RGBA", (480, 360), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((120, 70, 360, 290), fill=(40, 130, 220, 255))
    image.save(source)

    engine = Engine(Settings(data_root=tmp_path / "runtime"))
    job = engine.create_job([source], QualityMode.QUICK_2D)
    result = engine.run_job(job.job_id)

    assert result.state in {JobState.COMPLETED, JobState.REVIEW_REQUIRED}
    plan = engine.checkpoints.payload(job.job_id, "representation_plan")
    assert plan["method"] == "representation_plan_v1"
    assert plan["overall"] in {"vector", "raster", "hybrid"}
    assert plan["components"]

    candidate = engine.checkpoints.payload(job.job_id, "candidate")
    assert candidate["path"]
    assert "representation_plan" not in (candidate.get("precision_ops") or [])

    manifest_path = (
        engine.settings.jobs_dir
        / job.job_id
        / "master"
        / "artifact_manifest.json"
    )
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert '"representation_plan"' in manifest_text
