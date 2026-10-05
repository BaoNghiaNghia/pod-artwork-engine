import json
from pathlib import Path

from PIL import Image, ImageDraw

from pod_artwork_engine.dataset_registry import DatasetRegistry
from pod_artwork_engine.historical_import import HistoricalImporter, pairing_key


def _save(path: Path, color: tuple[int, int, int]) -> Path:
    image = Image.new("RGB", (80, 80), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((15, 15, 65, 65), fill=color)
    image.save(path)
    return path


def test_pairing_key_removes_export_and_view_suffixes() -> None:
    assert pairing_key(Path("design-001-front.jpg")) == "design-001"
    assert pairing_key(Path("design-001-final.png")) == "design-001"
    assert pairing_key(Path("design-001-front-1.jpg")) == "design-001"


def test_folder_import_pairs_multiple_sources_to_approved_final(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    target_dir = tmp_path / "finals"
    source_dir.mkdir()
    target_dir.mkdir()

    _save(source_dir / "design-001-front.jpg", (10, 80, 150))
    _save(source_dir / "design-001-back.jpg", (20, 90, 160))
    _save(target_dir / "design-001-final.png", (200, 40, 60))
    _save(source_dir / "unmatched-front.jpg", (40, 40, 40))

    registry = DatasetRegistry(tmp_path / "engine.sqlite3", tmp_path / "datasets")
    report = HistoricalImporter(registry).import_folders(
        source_dir,
        target_dir,
        allow_visual_fallback=False,
    )

    assert len(report.imported_pair_ids) == 1
    pair = registry.get_pair(report.imported_pair_ids[0])
    assert pair is not None
    assert pair.pair_key == "design-001"
    assert len(pair.source_asset_ids) == 2
    assert report.dataset is not None
    assert any("unmatched-front.jpg" in path for path in report.unmatched_sources)


def test_manifest_import_supports_explicit_design_ids_and_metadata(tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    finals = tmp_path / "finals"
    refs.mkdir()
    finals.mkdir()

    source = _save(refs / "phone-photo.png", (30, 100, 170))
    target = _save(finals / "clean-master.png", (180, 70, 20))
    manifest = tmp_path / "pairs.json"
    manifest.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "design_id": "SKU-ABC-42",
                        "target": str(target.relative_to(tmp_path)),
                        "sources": [str(source.relative_to(tmp_path))],
                        "metadata": {"approved_by": "historical"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    registry = DatasetRegistry(tmp_path / "engine.sqlite3", tmp_path / "datasets")
    report = HistoricalImporter(registry).import_manifest(manifest)

    assert len(report.imported_pair_ids) == 1
    pair = registry.get_pair(report.imported_pair_ids[0])
    assert pair is not None
    assert pair.pair_key == "SKU-ABC-42"
    assert pair.metadata["approved_by"] == "historical"
    assert report.dataset is not None
