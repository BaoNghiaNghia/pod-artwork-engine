import json
import zipfile
from pathlib import Path

from pod_artwork_engine.diagnostics import build_diagnostic_bundle
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.updater import UpdateManager


def test_diagnostic_bundle_includes_update_state(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")
    manager = UpdateManager(settings, "1.0.0")
    manager._write_state(
        {
            "current_version": "1.0.0",
            "previous_version": None,
            "staged_version": None,
            "channel": "stable",
            "last_error": None,
        }
    )

    archive = build_diagnostic_bundle(settings, tmp_path / "diag.zip")
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert "update-state.json" in names
        payload = json.loads(bundle.read("update-state.json").decode("utf-8"))
        assert payload["current_version"] == "1.0.0"
