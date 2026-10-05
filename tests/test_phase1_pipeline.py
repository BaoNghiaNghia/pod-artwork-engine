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
    GeometryKind,
    GeometryPathCommand,
    GeometryPrimitive,
    GeometrySpec,
    JobState,
    NormalizedPoint,
    PathCommandKind,
    ProviderResult,
    QualityMode,
    RegionReplacementMode,
    RouteKind,
    SemanticJudgeResult,
    TypographyLine,
    TypographySpec,
)
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.geometry import geometry_to_svg, render_geometry_master
from pod_artwork_engine.local_ocr import LocalOCRResult
from pod_artwork_engine.preflight import inspect_image
from pod_artwork_engine.router import choose_route
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.typed_control import TypedBoundaryError, validate_typed_payload
from pod_artwork_engine.typography import (
    render_typography_master,
    replace_mixed_typography,
    resolve_font,
)


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
    draw.rectangle((size // 6, size // 4, size * 5 // 6, size * 3 // 4), fill=(60, 120, 220, 255))
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


def test_max_fidelity_runs_semantic_judge_and_merges_ocr(monkeypatch, tmp_path: Path) -> None:
    source = _opaque_mockup(tmp_path / "reference-max.png")
    settings = Settings(
        data_root=tmp_path / "data-max",
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
                model_version="test-v2",
                design_spec=remote_spec,
                recognized_text=["HELLO"],
            )
        if request.action == "reconstruct":
            return ProviderResult(
                provider="test-provider",
                model_version="test-v2",
                design_spec=remote_spec,
                candidate_image_base64=_png_base64(1800),
                recognized_text=["HELLO"],
            )
        assert request.action == "judge"
        assert request.candidate_path
        return ProviderResult(
            provider="test-provider",
            model_version="test-v2",
            recognized_text=["HELLO"],
            judge_result=SemanticJudgeResult(
                exact_text=1.0,
                layout=0.95,
                object_fidelity=0.94,
                color=0.96,
                texture=0.92,
                missing_detail=0.93,
                confidence=0.97,
            ),
        )

    monkeypatch.setattr(engine.provider, "execute", fake_execute)

    job = engine.create_job([source], QualityMode.MAX_FIDELITY)
    result = engine.run_job(job.job_id)

    assert calls == ["analyze", "reconstruct", "judge"]
    assert result.state is JobState.COMPLETED, result.failure_reason

    design_spec = engine.checkpoints.payload(job.job_id, "design_spec")
    assert design_spec["exact_text"] == ["HELLO"]

    judge = engine.checkpoints.payload(job.job_id, "semantic_judge")
    assert judge["judge_result"]["object_fidelity"] == 0.94

    semantic = engine.checkpoints.payload(job.job_id, "qc_semantic")
    assert semantic["metrics"]["exact_text_verified"] is True
    assert semantic["metrics"]["object_fidelity"] == 0.94


def test_deterministic_typography_renders_only_with_matched_font(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "typography-data")
    settings.ensure_directories()
    font = resolve_font(settings, "Arial")
    if font is None:
        pytest.skip("Arial is not available on this host")

    spec = TypographySpec(
        line_order_confidence=0.98,
        font_match_confidence=0.95,
        lines=[
            TypographyLine(
                text="HELLO POD",
                bbox=BoundingBox(x=0.1, y=0.35, width=0.8, height=0.3),
                font_family="Arial",
                font_weight=700,
                fill="#111111",
                confidence=0.97,
            )
        ],
    )
    output = render_typography_master(
        spec,
        settings,
        tmp_path / "typography.png",
        canvas_size=(1200, 800),
    )

    with Image.open(output) as image:
        assert image.mode == "RGBA"
        assert image.getchannel("A").getbbox() is not None


def test_quick_typography_uses_deterministic_redraw_when_font_matches(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _transparent_art(tmp_path / "text-reference.png")
    settings = Settings(
        data_root=tmp_path / "text-data",
        remote_provider_url="http://provider.invalid/gateway",
        remote_provider_name="test-provider",
    )
    settings.ensure_directories()
    if resolve_font(settings, "Arial") is None:
        pytest.skip("Arial is not available on this host")

    engine = Engine(settings)
    calls: list[str] = []
    typography = TypographySpec(
        line_order_confidence=0.98,
        font_match_confidence=0.95,
        lines=[
            TypographyLine(
                text="HELLO POD",
                bbox=BoundingBox(x=0.08, y=0.35, width=0.84, height=0.30),
                font_family="Arial",
                font_weight=700,
                fill="#111111",
                confidence=0.98,
            )
        ],
    )
    remote_spec = DesignSpec(
        artwork_type=ArtworkType.TYPOGRAPHY,
        artwork_bbox=BoundingBox(x=0.15, y=0.12, width=0.70, height=0.76),
        exact_text=["HELLO POD"],
        typography=typography,
        confidence=0.96,
        required_capabilities=["need_exact_text", "need_vector"],
    )

    def fake_execute(request):
        calls.append(request.action)
        assert request.action == "analyze"
        return ProviderResult(
            provider="test-provider",
            model_version="test-text-v1",
            design_spec=remote_spec,
            recognized_text=["HELLO POD"],
        )

    monkeypatch.setattr(engine.provider, "execute", fake_execute)

    job = engine.create_job([source], QualityMode.QUICK_2D)
    result = engine.run_job(job.job_id)

    assert calls == ["analyze"]
    assert result.state is JobState.COMPLETED
    candidate = engine.checkpoints.payload(job.job_id, "candidate")
    assert candidate["used_remote"] is False
    assert candidate["recognized_text"] == ["HELLO POD"]
    assert candidate["alpha_method"] == "provider_or_existing_alpha"


def test_mixed_text_replacement_changes_only_explicit_safe_region(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "mixed-data")
    settings.ensure_directories()
    if resolve_font(settings, "Arial") is None:
        pytest.skip("Arial is not available on this host")

    candidate_path = tmp_path / "mixed-candidate.png"
    base = Image.new("RGBA", (800, 600), (30, 110, 70, 255))
    draw = ImageDraw.Draw(base)
    draw.ellipse((40, 40, 220, 220), fill=(220, 80, 90, 255))
    base.save(candidate_path)

    spec = TypographySpec(
        line_order_confidence=0.98,
        font_match_confidence=0.94,
        evidence_provider="fixture",
        evidence_version="1",
        lines=[
            TypographyLine(
                text="SAFE TEXT",
                bbox=BoundingBox(x=0.40, y=0.38, width=0.45, height=0.18),
                font_family="Arial",
                font_weight=700,
                fill="#111111",
                confidence=0.97,
                replacement_mode=RegionReplacementMode.REPLACE_SOLID,
                replacement_fill="#ffffff",
            )
        ],
    )

    output, processed = replace_mixed_typography(
        candidate_path,
        spec,
        settings,
        tmp_path / "mixed-refined.png",
    )

    with Image.open(candidate_path) as before, Image.open(output) as after:
        before = before.convert("RGBA")
        after = after.convert("RGBA")
        assert before.getpixel((80, 80)) == after.getpixel((80, 80))
        assert before.getpixel((325, 235)) != after.getpixel((325, 235))

    assert processed == ["SAFE TEXT"]


def test_geometry_renderer_creates_raster_and_svg_master(tmp_path: Path) -> None:
    spec = GeometrySpec(
        confidence=0.96,
        primitives=[
            GeometryPrimitive(
                kind=GeometryKind.RECT,
                bbox=BoundingBox(x=0.15, y=0.18, width=0.70, height=0.18),
                fill="#111111",
                confidence=0.98,
            ),
            GeometryPrimitive(
                kind=GeometryKind.ELLIPSE,
                bbox=BoundingBox(x=0.30, y=0.42, width=0.40, height=0.40),
                fill="#f2c94c",
                stroke="#111111",
                confidence=0.97,
            ),
            GeometryPrimitive(
                kind=GeometryKind.LINE,
                points=[
                    NormalizedPoint(x=0.25, y=0.88),
                    NormalizedPoint(x=0.75, y=0.88),
                ],
                stroke="#111111",
                stroke_width_ratio=0.01,
                confidence=0.99,
            ),
        ],
    )

    png = render_geometry_master(
        spec,
        tmp_path / "geometry.png",
        canvas_size=(1200, 1200),
    )
    svg = geometry_to_svg(
        spec,
        tmp_path / "geometry.svg",
        view_box=(1200, 1200),
    )

    with Image.open(png) as image:
        assert image.mode == "RGBA"
        assert image.getchannel("A").getbbox() is not None

    svg_text = svg.read_text(encoding="utf-8")
    assert "<rect " in svg_text
    assert "<ellipse " in svg_text
    assert "<line " in svg_text


def test_quick_logo_uses_deterministic_geometry_after_remote_analysis(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _transparent_art(tmp_path / "logo-reference.png")
    settings = Settings(
        data_root=tmp_path / "logo-data",
        remote_provider_url="http://provider.invalid/gateway",
        remote_provider_name="test-provider",
    )
    engine = Engine(settings)
    calls: list[str] = []

    geometry = GeometrySpec(
        confidence=0.97,
        primitives=[
            GeometryPrimitive(
                kind=GeometryKind.ELLIPSE,
                bbox=BoundingBox(x=0.20, y=0.20, width=0.60, height=0.60),
                fill="#111111",
                confidence=0.98,
            ),
            GeometryPrimitive(
                kind=GeometryKind.ELLIPSE,
                bbox=BoundingBox(x=0.28, y=0.28, width=0.44, height=0.44),
                fill="#ffffff",
                confidence=0.98,
            ),
        ],
    )
    remote_spec = DesignSpec(
        artwork_type=ArtworkType.LOGO,
        artwork_bbox=BoundingBox(x=0.15, y=0.12, width=0.70, height=0.76),
        geometry=geometry,
        confidence=0.97,
        required_capabilities=["need_vector"],
    )

    def fake_execute(request):
        calls.append(request.action)
        assert request.action == "analyze"
        return ProviderResult(
            provider="test-provider",
            model_version="test-logo-v1",
            design_spec=remote_spec,
        )

    monkeypatch.setattr(engine.provider, "execute", fake_execute)

    job = engine.create_job([source], QualityMode.QUICK_2D)
    result = engine.run_job(job.job_id)

    assert calls == ["analyze"]
    assert result.state is JobState.COMPLETED
    candidate = engine.checkpoints.payload(job.job_id, "candidate")
    assert candidate["used_remote"] is False

    geometry_svg = (
        engine.settings.jobs_dir
        / job.job_id
        / "master"
        / "vector"
        / "geometry.svg"
    )
    assert geometry_svg.is_file()

    manifest = (
        engine.settings.jobs_dir
        / job.job_id
        / "master"
        / "artifact_manifest.json"
    ).read_text(encoding="utf-8")
    assert "geometry_svg" in manifest


def test_mixed_text_replacement_supports_explicit_polygon_mask(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "masked-data")
    settings.ensure_directories()
    if resolve_font(settings, "Arial") is None:
        pytest.skip("Arial is not available on this host")

    candidate_path = tmp_path / "masked-candidate.png"
    base = Image.new("RGBA", (800, 600), (35, 115, 75, 255))
    ImageDraw.Draw(base).ellipse((40, 40, 210, 210), fill=(220, 80, 90, 255))
    base.save(candidate_path)

    spec = TypographySpec(
        line_order_confidence=0.98,
        font_match_confidence=0.94,
        evidence_provider="fixture",
        evidence_version="1",
        lines=[
            TypographyLine(
                text="MASK TEXT",
                bbox=BoundingBox(x=0.40, y=0.38, width=0.45, height=0.18),
                font_family="Arial",
                font_weight=700,
                fill="#111111",
                confidence=0.97,
                replacement_mode=RegionReplacementMode.REPLACE_MASK,
                replacement_fill="#ffffff",
                replacement_mask=[
                    NormalizedPoint(x=0.39, y=0.36),
                    NormalizedPoint(x=0.87, y=0.36),
                    NormalizedPoint(x=0.83, y=0.59),
                    NormalizedPoint(x=0.42, y=0.57),
                ],
            )
        ],
    )

    output, processed = replace_mixed_typography(
        candidate_path,
        spec,
        settings,
        tmp_path / "masked-refined.png",
    )

    with Image.open(candidate_path) as before, Image.open(output) as after:
        before = before.convert("RGBA")
        after = after.convert("RGBA")
        assert before.getpixel((80, 80)) == after.getpixel((80, 80))
        assert before.getpixel((350, 240)) != after.getpixel((350, 240))

    assert processed == ["MASK TEXT"]


def test_geometry_renderer_supports_cubic_bezier_path(tmp_path: Path) -> None:
    path_primitive = GeometryPrimitive(
        kind=GeometryKind.PATH,
        path=[
            GeometryPathCommand(
                kind=PathCommandKind.MOVE,
                points=[NormalizedPoint(x=0.15, y=0.55)],
            ),
            GeometryPathCommand(
                kind=PathCommandKind.CUBIC,
                points=[
                    NormalizedPoint(x=0.30, y=0.10),
                    NormalizedPoint(x=0.70, y=0.10),
                    NormalizedPoint(x=0.85, y=0.55),
                ],
            ),
            GeometryPathCommand(
                kind=PathCommandKind.CUBIC,
                points=[
                    NormalizedPoint(x=0.70, y=0.90),
                    NormalizedPoint(x=0.30, y=0.90),
                    NormalizedPoint(x=0.15, y=0.55),
                ],
            ),
            GeometryPathCommand(kind=PathCommandKind.CLOSE),
        ],
        fill="#f2c94c",
        stroke="#111111",
        stroke_width_ratio=0.008,
        confidence=0.98,
    )
    spec = GeometrySpec(primitives=[path_primitive], confidence=0.97)

    png = render_geometry_master(
        spec,
        tmp_path / "bezier.png",
        canvas_size=(1200, 1200),
    )
    svg = geometry_to_svg(
        spec,
        tmp_path / "bezier.svg",
        view_box=(1200, 1200),
    )

    with Image.open(png) as image:
        assert image.getchannel("A").getbbox() is not None

    svg_text = svg.read_text(encoding="utf-8")
    assert "<path " in svg_text
    assert " C " in svg_text or 'd="M ' in svg_text and "C " in svg_text


def test_engine_merges_high_confidence_local_ocr_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _transparent_art(tmp_path / "ocr-reference.png")
    settings = Settings(
        data_root=tmp_path / "ocr-runtime",
        local_ocr_enabled=True,
    )
    engine = Engine(settings)
    typography = TypographySpec(
        lines=[
            TypographyLine(
                text="HELLO",
                bbox=BoundingBox(x=0.2, y=0.35, width=0.6, height=0.2),
                confidence=0.96,
            )
        ],
        line_order_confidence=0.96,
        font_match_confidence=0.0,
        evidence_provider="tesseract",
        evidence_version="fixture",
    )

    monkeypatch.setattr(
        "pod_artwork_engine.engine.analyze_locally",
        lambda source_paths, preflights: DesignSpec(
            artwork_type=ArtworkType.MIXED,
            artwork_bbox=BoundingBox(x=0.15, y=0.15, width=0.70, height=0.70),
            confidence=0.85,
            required_capabilities=["need_semantic_reconstruction"],
        ),
    )
    monkeypatch.setattr(
        "pod_artwork_engine.engine.local_ocr_available",
        lambda settings: True,
    )
    monkeypatch.setattr(
        "pod_artwork_engine.engine.analyze_artwork_text",
        lambda source_path, artwork_bbox, settings: LocalOCRResult(
            exact_text=["HELLO"],
            typography=typography,
            backend="tesseract",
            backend_version="fixture",
        ),
    )

    job = engine.create_job([source], QualityMode.QUICK_2D)
    result = engine.run_job(job.job_id)

    assert result.state in {JobState.COMPLETED, JobState.REVIEW_REQUIRED}
    design_spec = engine.checkpoints.payload(job.job_id, "design_spec")
    assert design_spec["exact_text"] == ["HELLO"]
    assert design_spec["typography"]["evidence_provider"] == "tesseract"
    local_ocr = engine.checkpoints.payload(job.job_id, "local_ocr")
    assert local_ocr["exact_text"] == ["HELLO"]
    candidate = engine.checkpoints.payload(job.job_id, "candidate")
    assert candidate["recognized_text"] == ["HELLO"]
