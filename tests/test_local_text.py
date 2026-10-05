from __future__ import annotations

from pathlib import Path

from pod_artwork_engine.font_catalog import FontCatalog, FontEntry
from pod_artwork_engine.local_ocr import _parse_tsv
from pod_artwork_engine.settings import Settings


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
