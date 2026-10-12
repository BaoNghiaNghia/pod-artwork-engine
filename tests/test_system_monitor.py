from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from pod_artwork_engine.api import create_app
from pod_artwork_engine.logging_config import (
    PerJobJsonHandler, JOB_LOG_RETENTION_DAYS, LoggingRuntime, log_event,
)
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.system_monitor import log_summary, recent_safe_events
from pod_artwork_engine.updater import ReleaseManifest, UpdateCheck, UpdateManager


def test_per_job_log_escapes_are_rejected_and_files_rotate(tmp_path: Path, monkeypatch) -> None:
    import pod_artwork_engine.logging_config as config
    monkeypatch.setattr(config, "JOB_LOG_MAX_BYTES", 800)
    handler = PerJobJsonHandler(tmp_path / "jobs")
    logger = logging.getLogger("test-rotation")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for i in range(30):
            log_event(logger, "edit_completed", job_id="safe-job", stage="generation",
                      duration_ms=i, route="AI")
        log_event(logger, "do_not_write", job_id="../escape")
        assert (tmp_path / "jobs" / "safe-job.jsonl").is_file()
        assert (tmp_path / "jobs" / "safe-job.jsonl.1").is_file()
        assert not (tmp_path / "escape.jsonl").exists()
        assert len(list((tmp_path / "jobs").glob("safe-job.jsonl*"))) <= 3
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_expired_job_log_pruned_at_start(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "jobs"
    log_dir.mkdir(parents=True)
    stale = log_dir / "oldjob.jsonl"
    stale.write_text("old", encoding="utf-8")
    os.utime(stale, (time.time() - 86400 * (JOB_LOG_RETENTION_DAYS + 1),) * 2)
    runtime = LoggingRuntime(tmp_path / "logs")
    runtime.start()
    try:
        assert not stale.exists()
    finally:
        runtime.stop()


def test_logging_runtime_restores_original_root_handlers(tmp_path: Path) -> None:
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    runtime = LoggingRuntime(tmp_path / "logs")
    runtime.start()
    assert root.handlers != handlers
    runtime.stop()
    assert root.handlers == handlers
    assert root.level == level


def test_monitor_never_exposes_raw_log_messages(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path)
    settings.ensure_directories()
    secret = "sk-test-secret-never-expose"
    (settings.logs_dir / "engine.log").write_text(json.dumps({
        "timestamp": "2026-10-12T00:00:00+00:00",
        "message": f"User prompt {secret}", "exception": secret,
        "level": "INFO", "event": "tab_opened", "stage": "chat",
    }) + "\n", encoding="utf-8")
    summary = log_summary(settings)
    assert summary["files"][0]["name"] == "engine.log"
    assert summary["job_retention_days"] == JOB_LOG_RETENTION_DAYS
    safe = recent_safe_events(settings)
    assert safe and safe[0]["event"] == "tab_opened"
    assert secret not in json.dumps(safe)
    assert "message" not in safe[0] and "exception" not in safe[0]


def test_local_updates_check_stage_and_event_validation(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(data_root=tmp_path, release_manifest_url="https://example.test/manifest.json")
    check = UpdateCheck(
        enabled=True, update_available=True,
        current_version="0.1.11", latest_version="0.1.12",
        channel="stable", reason="update available",
    )
    manifest = ReleaseManifest(
        version="0.1.12", channel="stable",
        package_url="https://example.test/release.zip", sha256="a" * 64,
    )
    monkeypatch.setattr(UpdateManager, "check", lambda self: check)
    monkeypatch.setattr(UpdateManager, "fetch_manifest", lambda self: manifest)
    monkeypatch.setattr(UpdateManager, "stage", lambda self, item: tmp_path / "safe-staged")
    with TestClient(create_app(settings)) as client:
        overview = client.get("/system/overview")
        assert overview.status_code == 200
        assert overview.json()["logs"]["directory"] == str(settings.logs_dir)
        event = client.post("/system/events", json={"event": "tab_opened", "screen": "chat"})
        assert event.status_code == 204
        assert client.post("/system/events", json={
            "event": "arbitrary_user_prompt", "screen": "chat", "secret": "ignored",
        }).status_code == 422
        result = client.post("/system/check-updates")
        assert result.status_code == 200
        assert result.json()["latest_version"] == "0.1.12"
        assert result.json()["checked_at"]
        stage = client.post("/system/stage-update")
        assert stage.status_code == 200
        assert stage.json() == {"status": "restart_required", "version": "0.1.12"}


def test_update_failure_does_not_expose_secrets(tmp_path: Path, monkeypatch) -> None:
    def fail(self):
        raise RuntimeError("sk-super-secret https://url.example?token=private")
    monkeypatch.setattr(UpdateManager, "check", fail)
    with TestClient(create_app(Settings(data_root=tmp_path))) as client:
        result = client.post("/system/check-updates")
        assert result.status_code == 503
        assert "sk-super-secret" not in result.text
        assert "token=private" not in result.text
