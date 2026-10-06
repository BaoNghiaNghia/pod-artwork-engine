from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from pod_artwork_engine.contracts import (
    BoundingBox,
    FontMatchEvidence,
    NormalizedPoint,
    RegionReplacementMode,
    TypographyLine,
    TypographySpec,
)
from pod_artwork_engine.font_catalog import FontCatalog, FontEntry, normalize_font_name
from pod_artwork_engine.font_matcher import (
    match_typography_fonts,
    merge_verified_font_matches,
)
from pod_artwork_engine.local_ocr import _parse_tsv
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.typography import (
    TypographyRenderUnavailable,
    apply_typography,
    render_typography_master,
)


def test_local_ocr_tsv_parser_builds_ordered_normalized_lines() -> None:
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t100\t120\t120\t50\t96\tHELLO\n"
        "5\t1\t1\t1\t1\t2\t240\t120\t90\t50\t94\tPOD\n"
        "5\t1\t1\t1\t2\t1\t150\t240\t180\t55\t92\tWORLD\n"
        "5\t1\t1\t1\t3\t1\t20\t20\t30\t20\t45\tNOISE\n"
    )

    result = _parse_tsv(tsv, width=500, height=400)

    assert result.exact_text == ["HELLO POD", "WORLD"]
    assert result.typography.line_order_confidence > 0.9
    assert result.typography.font_match_confidence == 0
    assert result.typography.evidence_provider == "tesseract"
    first = result.typography.lines[0]
    assert 0.19 <= first.bbox.x <= 0.21
    assert 0.29 <= first.bbox.y <= 0.31
    assert 0.45 <= first.bbox.width <= 0.47


def test_font_catalog_normalizes_alias_but_never_downgrades_bold(
    monkeypatch,
    tmp_path: Path,
) -> None:
    regular = FontEntry(
        path=tmp_path / "Arial-Regular.ttf",
        family="Arial",
        style="Regular",
        normalized_family="arial",
        normalized_stem="arialregular",
        inferred_weight=400,
    )
    bold = FontEntry(
        path=tmp_path / "Arial-Bold.ttf",
        family="Arial",
        style="Bold",
        normalized_family="arial",
        normalized_stem="arialbold",
        inferred_weight=700,
    )
    monkeypatch.setattr(FontCatalog, "_scan", lambda self: [regular, bold])
    catalog = FontCatalog(Settings(data_root=tmp_path / "data"))

    assert catalog.canonicalize("ArialMT") == "arial"
    assert catalog.resolve("ArialMT", 400) == regular
    assert catalog.resolve("Arial-BoldMT", 700) == bold

    monkeypatch.setattr(FontCatalog, "_scan", lambda self: [regular])
    regular_only = FontCatalog(Settings(data_root=tmp_path / "data-2"))
    assert regular_only.resolve("Arial", 700) is None


def _fixture_font_path(tmp_path: Path) -> Path:
    for name in ("DejaVuSans.ttf", "Arial.ttf", "LiberationSans-Regular.ttf"):
        try:
            font = ImageFont.truetype(name, 96)
        except OSError:
            continue
        path = Path(str(getattr(font, "path", "")))
        if path.is_file():
            return path

    catalog = FontCatalog(Settings(data_root=tmp_path / "font-discovery"))
    if catalog.entries:
        return catalog.entries[0].path
    pytest.skip("no fixture TrueType font is available")


def _font_entry(path: Path, family: str) -> FontEntry:
    return FontEntry(
        path=path.resolve(),
        family=family,
        style="Regular",
        normalized_family=normalize_font_name(family),
        normalized_stem=normalize_font_name(path.stem),
        inferred_weight=400,
    )


def _render_font_fixture(
    tmp_path: Path,
    font_path: Path,
    text: str = "HELLO POD",
) -> tuple[Path, TypographySpec]:
    image = Image.new("RGBA", (900, 320), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 118)
    text_box = draw.textbbox((110, 86), text, font=font)
    draw.text((110, 86), text, font=font, fill=(20, 20, 20, 255))
    path = tmp_path / "font-reference.png"
    image.save(path)

    left, top, right, bottom = text_box
    spec = TypographySpec(
        lines=[
            TypographyLine(
                text=text,
                bbox=BoundingBox(
                    x=left / image.width,
                    y=top / image.height,
                    width=(right - left) / image.width,
                    height=(bottom - top) / image.height,
                ),
                confidence=0.98,
            )
        ],
        line_order_confidence=0.98,
        evidence_provider="tesseract",
        evidence_version="fixture",
    )
    return path, spec


def test_visual_font_match_accepts_exact_rendered_font(
    monkeypatch,
    tmp_path: Path,
) -> None:
    font_path = _fixture_font_path(tmp_path)
    source, spec = _render_font_fixture(tmp_path, font_path)
    entry = _font_entry(font_path, "Fixture Sans")
    monkeypatch.setattr(
        "pod_artwork_engine.font_matcher.get_font_catalog",
        lambda settings: type("Catalog", (), {"entries": [entry]})(),
    )
    settings = Settings(
        data_root=tmp_path / "runtime",
        visual_font_match_min_score=0.60,
        visual_font_match_min_margin=0.01,
        visual_font_match_max_candidates=16,
    )

    matched = match_typography_fonts(
        source,
        BoundingBox(x=0, y=0, width=1, height=1),
        spec,
        settings,
    )

    line = matched.lines[0]
    assert line.font_match is not None
    assert line.font_match.accepted is True
    assert line.font_match.family == "Fixture Sans"
    assert line.font_family == "Fixture Sans"
    assert line.font_match.score >= 0.60
    assert len(line.font_match.font_sha256) == 64
    assert matched.font_match_confidence >= 0.60
    assert "visual_render_compare_v1" in matched.evidence_provider


def test_visual_font_match_rejects_ambiguous_equal_candidates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    font_path = _fixture_font_path(tmp_path)
    source, spec = _render_font_fixture(tmp_path, font_path)
    entries = [
        _font_entry(font_path, "Fixture Sans A"),
        _font_entry(font_path, "Fixture Sans B"),
    ]
    monkeypatch.setattr(
        "pod_artwork_engine.font_matcher.get_font_catalog",
        lambda settings: type("Catalog", (), {"entries": entries})(),
    )
    settings = Settings(
        data_root=tmp_path / "runtime",
        visual_font_match_min_score=0.50,
        visual_font_match_min_margin=0.02,
        visual_font_match_max_candidates=16,
    )

    matched = match_typography_fonts(
        source,
        BoundingBox(x=0, y=0, width=1, height=1),
        spec,
        settings,
    )

    line = matched.lines[0]
    assert line.font_match is not None
    assert line.font_match.accepted is False
    assert line.font_match.margin < 0.02
    assert line.font_family == ""
    assert matched.font_match_confidence == 0


def test_verified_visual_font_evidence_overrides_provider_guess() -> None:
    provider = TypographySpec(
        lines=[
            TypographyLine(
                text="HELLO POD",
                bbox=BoundingBox(x=0.1, y=0.2, width=0.8, height=0.3),
                font_family="Provider Guess",
                font_weight=700,
                confidence=0.98,
            )
        ],
        line_order_confidence=0.98,
        font_match_confidence=0.82,
        evidence_provider="provider",
    )
    verified = TypographySpec.model_validate(
        {
            "lines": [
                {
                    "text": "HELLO POD",
                    "bbox": {"x": 0.1, "y": 0.2, "width": 0.8, "height": 0.3},
                    "font_family": "Verified Sans",
                    "font_weight": 400,
                    "confidence": 0.98,
                    "font_match": {
                        "family": "Verified Sans",
                        "style": "Regular",
                        "weight": 400,
                        "score": 0.91,
                        "margin": 0.08,
                        "accepted": True,
                        "method": "visual_render_compare_v1",
                        "candidates_evaluated": 40,
                    },
                }
            ],
            "line_order_confidence": 0.98,
            "font_match_confidence": 0.91,
            "evidence_provider": "tesseract+visual_render_compare_v1",
        }
    )

    merged = merge_verified_font_matches(provider, verified)

    assert merged.lines[0].font_family == "Verified Sans"
    assert merged.lines[0].font_weight == 400
    assert merged.lines[0].font_match is not None
    assert merged.lines[0].font_match.accepted is True
    assert merged.font_match_confidence == 0.91


def test_deterministic_typography_rejects_changed_visual_font_fingerprint(
    monkeypatch,
    tmp_path: Path,
) -> None:
    font_path = _fixture_font_path(tmp_path)
    entry = _font_entry(font_path, "Fixture Sans")
    monkeypatch.setattr(
        "pod_artwork_engine.typography.get_font_catalog",
        lambda settings: type(
            "Catalog",
            (),
            {
                "entries": [entry],
                "canonicalize": lambda self, family: normalize_font_name(family),
                "resolve": lambda self, family, weight=400: entry,
            },
        )(),
    )
    settings = Settings(data_root=tmp_path / "runtime")
    spec = TypographySpec(
        lines=[
            TypographyLine(
                text="HELLO",
                bbox=BoundingBox(x=0.1, y=0.2, width=0.8, height=0.3),
                font_family="Fixture Sans",
                font_weight=400,
                confidence=0.98,
                font_match=FontMatchEvidence(
                    family="Fixture Sans",
                    style="Regular",
                    weight=400,
                    score=0.95,
                    margin=0.12,
                    accepted=True,
                    method="visual_render_compare_v1",
                    candidates_evaluated=12,
                    font_sha256="0" * 64,
                ),
            )
        ],
        line_order_confidence=0.98,
        font_match_confidence=0.95,
    )

    with pytest.raises(TypographyRenderUnavailable):
        render_typography_master(
            spec,
            settings,
            tmp_path / "should-not-render.png",
            canvas_size=(1000, 600),
        )


def _repair_fixture() -> tuple[Image.Image, TypographySpec]:
    image = Image.new("RGBA", (120, 80), (0, 0, 0, 255))
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            value = round(30 + (180 * x / (image.width - 1)))
            pixels[x, y] = (value, 90, 160, 255)

    ImageDraw.Draw(image).rectangle((45, 28, 75, 52), fill=(5, 5, 5, 255))
    line = TypographyLine(
        text="REPAIRED",
        bbox=BoundingBox(x=0.35, y=0.30, width=0.30, height=0.40),
        confidence=0.98,
        replacement_mode=RegionReplacementMode.REPAIR_LOCAL,
        replacement_mask=[
            NormalizedPoint(x=0.375, y=0.35),
            NormalizedPoint(x=0.625, y=0.35),
            NormalizedPoint(x=0.625, y=0.65),
            NormalizedPoint(x=0.375, y=0.65),
        ],
        replacement_confidence=0.96,
    )
    spec = TypographySpec(
        lines=[line],
        line_order_confidence=0.98,
        font_match_confidence=0.95,
    )
    return image, spec


def test_local_text_repair_restores_gradient_from_boundary_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    image, spec = _repair_fixture()
    monkeypatch.setattr(
        "pod_artwork_engine.typography._draw_line",
        lambda canvas, line, settings: None,
    )
    settings = Settings(data_root=tmp_path / "repair-runtime")

    repaired, processed = apply_typography(
        image,
        spec,
        settings,
        safe_replacements_only=True,
    )

    assert processed == ["REPAIRED"]
    expected = round(30 + (180 * 60 / 119))
    center = repaired.getpixel((60, 40))
    assert abs(center[0] - expected) <= 2
    assert center[1:] == (90, 160, 255)
    assert repaired.getpixel((20, 20)) == image.getpixel((20, 20))


def test_local_text_repair_fails_closed_below_confidence_threshold(
    monkeypatch,
    tmp_path: Path,
) -> None:
    image, spec = _repair_fixture()
    spec.lines[0].replacement_confidence = 0.50
    monkeypatch.setattr(
        "pod_artwork_engine.typography._draw_line",
        lambda canvas, line, settings: None,
    )
    settings = Settings(data_root=tmp_path / "repair-runtime")

    with pytest.raises(
        TypographyRenderUnavailable,
        match="local repair confidence too low",
    ):
        apply_typography(
            image,
            spec,
            settings,
            safe_replacements_only=True,
        )
