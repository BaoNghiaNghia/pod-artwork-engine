from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pod_artwork_engine.contracts import (
    ArtworkType,
    BoundingBox,
    DesignSpec,
    JobState,
    ProviderResult,
    QualityMode,
    RouteKind,
)
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.preflight import inspect_image
from pod_artwork_engine.router import choose_route
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.typed_control import TypedBoundaryError, validate_typed_payload


def _transparent_art(path: Path, size: int = 1200) -> Path:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (size // 4, size // 3, size * 3 // 4, size * 2 // 3),
        radius=40,
        fill=(25, 80, 180, 255),
    )
    draw.ellipse(
        (size // 2 - 90, size // 2 - 90, size // 2 + 90, size // 2 + 90),
        fill=(245, 210, 60, 255),
    )
    image.save(path)
    return path


def _opaque_mockup(path: Path, size: int = 1200) -> Path:
    image = Image.new("RGB", (size, size), (225, 225, 225))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (size // 4, size // 3, size * 3 // 4, size * 2 // 3),
        fill=(40, 90, 185),
    )
    draw.ellipse(
        (size // 2 - 95, size // 2 - 95, size // 2 + 95, size // 2 + 95),
        fill=(245, 215, 70),
    )
    image.save(path)
    return path


def _png_base64(size: int = 1000) -> str:
    buffer = BytesIO()
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((180, 260, 820, 740), fill=(60, 120, 220, 255))
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def test_preflight_detects_transparent_artwork_region(tmp_path: Path) -> None:
    source = _transparent_art(tmp_path / "source.png")
    result = inspect_image(source)

    assert result.has_alpha
    assert result.artwork_bbox is not None
    assert result.artwork_confidence >= 0.5
    assert result.artwork_bbox.width < 0.8
    assert result.artwork_bbox.height < 0.8
    assert 0 <= result.source_quality <= 1


def test_typed_boundary_repairs_fenced_json_and_rejects_extra_fields() -> None:
    payload = (
        "```json\n"
        "{\n"
        "  \"artwork_type\": \"logo\",\n"
        "  \"artwork_bbox\": {\"x\": 0.1, \"y\": 0.2, \"width\": 0.5, \"height\": 0.4},\n"
        "  \"exact_text\": [],\n"
        "  \"objects\": [],\n"
        "  \"dominant_colors\": [\"#ffffff\"],\n"
        "  \"texture_classes\": [],\n"
        "  \"perspective_severity\": 0,\n"
        "  \"occlusion\": 0,\n"
        "  \"confidence\": 0.9,\n"
        "  \"required_capabilities\": []\n"
        "}\n"
        "```"
    )
    spec = validate_typed_payload(DesignSpec, payload)
    assert spec.artwork_type is ArtworkType.LOGO

    with pytest.raises(TypedBoundaryError):
        validate_typed_payload(
            DesignSpec,
            {
                **spec.model_dump(mode="json"),
                "unexpected": "not allowed",
            },
        )


def test_router_prefers_deterministic_high_confidence_logo() -> None:
    spec = DesignSpec(
        artwork_type=ArtworkType.LOGO,
        artwork_bbox=BoundingBox(x=0.2, y=0.2, width=0.6, height=0.5),
        confidence=0.9,
        required_capabilities=["need_vector"],
    )
    route = choose_route(spec, QualityMode.PRINT_READY, remote_available=True)
    assert route.route is RouteKind.DETERMINISTIC
    assert not route.use_remote_provider


def test_engine_produces_print_master_from_clean_transparent_input(tmp_path: Path) -> None:
    source = _transparent_art(tmp_path / "clean.png")
    engine = Engine(Settings(data_root=tmp_path / "data"))
    job = engine.create_job([source], QualityMode.PRINT_READY)

    result = engine.run_job(job.job_id)

    assert result.state in {JobState.COMPLETED, JobState.REVIEW_REQUIRED}
    assert result.result_path is not None
    output = Path(result.result_path)
    assert output.is_file()

    with Image.open(output) as image:
        assert image.size == (4500, 5400)
        assert image.mode == "RGBA"
        assert image.getchannel("A").getextrema()[0] == 0
        dpi = image.info.get("dpi", (0, 0))
        assert 295 <= dpi[0] <= 305
        assert 295 <= dpi[1] <= 305

    job_dir = engine.settings.jobs_dir / job.job_id
    assert (job_dir / "checkpoints" / "design_spec.json").is_file()
    assert (job_dir / "checkpoints" / "route.json").is_file()
    assert (job_dir / "checkpoints" / "qc_semantic.json").is_file()
    assert (job_dir / "checkpoints" / "qc_technical.json").is_file()
    assert (job_dir / "master" / "artifact_manifest.json").is_file()


def test_quick_2d_uses_configured_remote_provider(monkeypatch, tmp_path: Path) -> None:
    source = _opaque_mockup(tmp_path / "reference.png")
    settings = Settings(
        data_root=tmp_path / "data",
        remote_provider_url="http://provider.invalid/gateway",
        remote_provider_name="test-provider",
    )
    engine = Engine(settings)
    calls: list[str] = []

    remote_spec = DesignSpec(
        artwork_type=ArtworkType.ILLUSTRATION,
        artwork_bbox=BoundingBox(x=0.2, y=0.2, width=0.6, height=0.6),
        dominant_colors=["#3c78dc"],
        confidence=0.92,
        required_capabilities=["need_semantic_reconstruction"],
    )

    def fake_execute(request):
        calls.append(request.action)
        if request.action == "analyze":
            return ProviderResult(
                provider="test-provider",
                model_version="test-v1",
                design_spec=remote_spec,
            )
        return ProviderResult(
            provider="test-provider",
            model_version="test-v1",
            design_spec=remote_spec,
            candidate_image_base64=_png_base64(),
        )

    monkeypatch.setattr(engine.provider, "execute", fake_execute)

    job = engine.create_job([source], QualityMode.QUICK_2D)
    result = engine.run_job(job.job_id)

    assert calls == ["analyze", "reconstruct"]
    assert result.state is JobState.COMPLETED
    candidate_checkpoint = engine.checkpoints.payload(job.job_id, "candidate")
    assert candidate_checkpoint["used_remote"] is True
    assert candidate_checkpoint["provider"] == "test-provider"
