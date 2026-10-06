from __future__ import annotations

import zipfile
from pathlib import Path

from scripts.assemble_release import assemble
from pod_artwork_engine.updater import DESKTOP_EXECUTABLE, ENGINE_EXECUTABLE


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_release_assembly_includes_optional_bundled_ocr_runtime(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "build" / "bootstrap" / "PODArtworkTool.exe", b"launcher")
    _write(
        tmp_path / "desktop" / "src-tauri" / "target" / "release" / DESKTOP_EXECUTABLE,
        b"desktop",
    )
    _write(tmp_path / "build" / "engine" / ENGINE_EXECUTABLE, b"engine")
    _write(tmp_path / "vendor" / "tesseract" / "tesseract.exe", b"tesseract")
    _write(
        tmp_path / "vendor" / "tesseract" / "tessdata" / "eng.traineddata",
        b"eng",
    )

    result = assemble(tmp_path)

    assert result["ocr_runtime_included"] is True
    release = Path(result["release_dir"])
    assert (release / "runtime" / "tesseract" / "tesseract.exe").is_file()
    assert (
        release / "runtime" / "tesseract" / "tessdata" / "eng.traineddata"
    ).is_file()

    with zipfile.ZipFile(result["package"]) as archive:
        names = set(archive.namelist())
    assert DESKTOP_EXECUTABLE in names
    assert ENGINE_EXECUTABLE in names
    assert "runtime/tesseract/tesseract.exe" in names
    assert "runtime/tesseract/tessdata/eng.traineddata" in names


def test_release_assembly_remains_valid_without_optional_ocr_runtime(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "build" / "bootstrap" / "PODArtworkTool.exe", b"launcher")
    _write(
        tmp_path / "desktop" / "src-tauri" / "target" / "release" / DESKTOP_EXECUTABLE,
        b"desktop",
    )
    _write(tmp_path / "build" / "engine" / ENGINE_EXECUTABLE, b"engine")

    result = assemble(tmp_path)

    assert result["ocr_runtime_included"] is False
    release = Path(result["release_dir"])
    assert not (release / "runtime").exists()
