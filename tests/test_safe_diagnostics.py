from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from pod_artwork_engine.api import create_app
from pod_artwork_engine.safe_diagnostics import safe_diagnostic_report
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.system_monitor import recent_safe_events
from pod_artwork_engine.updater import UpdateManager


def test_diagnostic_report_redacts_secrets_and_paths(tmp_path):
    settings = Settings(data_root=tmp_path / "private-user-workspace")
    settings.ensure_directories()
    secret = "sk-secret-value-that-must-not-appear"
    (settings.logs_dir / "engine.log").write_text(
        json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR", "logger": "pod_artwork.audit",
            "event": "update_stage_failed", "stage": "HTTP_500",
            "message": secret, "exception": secret,
            "prompt": secret, "api_key": secret, "route": "/system/stage-update",
        }) + "\n",
        encoding="utf-8",
    )
    manager = UpdateManager(settings, "0.1.12")
    manager._write_state({
        "current_version": "0.1.12",
        "previous_version": "0.1.11",
        "last_error": secret,
        "last_success_at": "2026-10-12T07:00:00+00:00",
    })
    with TestClient(create_app(settings)) as client:
        response = client.get("/system/diagnostics")
        download = client.get("/system/diagnostics/download")
        assert response.status_code == download.status_code == 200
        assert download.headers["content-disposition"].startswith("attachment;")
        assert download.headers["cache-control"] == "no-store"
        payload = download.json()
        first = response.json()
        assert {k: v for k, v in payload.items() if k != "generated_at"} == {k: v for k, v in first.items() if k != "generated_at"}
        assert payload["update"]["has_last_error"] is True
        assert payload["recent_event_sample"]["error_or_failure_count"] >= 1
        serialized = json.dumps(payload)
        assert secret not in serialized
        assert str(settings.data_root) not in serialized
        assert "message" not in payload["recent_event_sample"]
        assert "events" not in payload["recent_event_sample"]


def test_rejects_malformed_or_unbounded_ui_events(tmp_path):
    settings = Settings(data_root=tmp_path)
    settings.ensure_directories()
    (settings.logs_dir / "engine.log").write_text(
        "\n".join(json.dumps(x) for x in [
            {"timestamp": datetime.now(timezone.utc).isoformat(),
             "level": "INFO", "event": "tab_opened", "stage": "artwork",
             "route": "/system/overview"},
            {"event": "tab_opened", "level": ["INFO"], "stage": ["key"]},
            {"event": "secret=" + "x" * 256, "message": "private"},
            {"event": "api_mutation", "route": "/?token=secret", "stage": "http_201"},
        ]) + "\n", encoding="utf-8",
    )
    events = recent_safe_events(settings)
    assert len(events) == 3
    assert events[0]["event"] == "tab_opened"
    assert "route" not in events[-1]
    assert all(isinstance(e.get("level", ""), str) for e in events)
    safe = safe_diagnostic_report(settings)
    assert safe["recent_event_sample"]["count"] >= 1
