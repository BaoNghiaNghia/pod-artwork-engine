import hashlib
import zipfile
from pathlib import Path

import pytest

from pod_artwork_engine.updater import _is_newer, _safe_extract_zip, verify_sha256


def test_version_comparison() -> None:
    assert _is_newer("0.2.0", "0.1.9")
    assert not _is_newer("0.1.0", "0.1.0")


def test_sha256_verification(tmp_path: Path) -> None:
    path = tmp_path / "package.zip"
    path.write_bytes(b"pod")
    expected = hashlib.sha256(b"pod").hexdigest()
    assert verify_sha256(path, expected)


def test_zip_slip_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../escape.txt", "bad")

    with pytest.raises(ValueError):
        _safe_extract_zip(archive, tmp_path / "out")
