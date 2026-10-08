from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pod_artwork_engine.api import create_app
from pod_artwork_engine.pair_previews import PairPreviewSessions
from pod_artwork_engine.settings import Settings


def _prepare(tmp_path: Path) -> tuple[TestClient, dict, Path]:
    source = tmp_path / "sources"
    target = tmp_path / "finals"
    source.mkdir()
    target.mkdir()
    for index in range(4):
        Image.new("RGB", (108, 108), (25 + index * 30, 80, 95)).save(
            source / f"design{index}_front.png"
        )
        Image.new("RGBA", (108 + index * 12, 110), (180, 25 + index * 30, 80, 220)).save(
            target / f"design{index}_final.png"
        )
    return (
        TestClient(create_app(Settings(data_root=tmp_path / "runtime"))),
        {
            "source_dir": str(source),
            "target_dir": str(target),
            "minimum_golden_cases": 1,
        },
        source,
    )


def test_pair_thumbnails_are_small_local_and_read_only(tmp_path: Path) -> None:
    client, request, _ = _prepare(tmp_path)
    response = client.post("/historical/onboarding/preflight", json=request)
    assert response.status_code == 200
    result = response.json()
    assert result["preview_token"]
    assert result["snapshot_id"]
    assert len(result["report"]["pairs"]) == 4
    token = result["preview_token"]
    first = client.get(f"/historical/onboarding/previews/{token}/0/source")
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/webp"
    assert first.headers["cache-control"] == "private, no-store"
    with Image.open(BytesIO(first.content)) as image:
        assert image.format == "WEBP"
        assert max(image.size) <= 384
    target = client.get(f"/historical/onboarding/previews/{token}/0/target")
    assert target.status_code == 200
    full = client.get(f"/historical/onboarding/previews/{token}/0/source?size=large")
    assert full.status_code == 200
    with Image.open(BytesIO(full.content)) as enlarged:
        assert max(enlarged.size) <= 1200
    assert client.get(
        f"/historical/onboarding/previews/{token}/0/source?size=original"
    ).status_code == 404
    assert client.get("/datasets").json() == []
    assert client.get("/historical/pairs").json() == []


def test_preview_rejects_invalid_token_index_role_and_source(tmp_path: Path) -> None:
    client, request, _ = _prepare(tmp_path)
    token = client.post("/historical/onboarding/preflight", json=request).json()["preview_token"]
    assert token
    assert client.get("/historical/onboarding/previews/invalid/0/source").status_code == 404
    assert client.get(f"/historical/onboarding/previews/{token}/100/source").status_code == 404
    assert client.get(f"/historical/onboarding/previews/{token}/0/other").status_code == 404
    assert client.get(
        f"/historical/onboarding/previews/{token}/0/source?source_index=999"
    ).status_code == 404
    assert client.get(f"/historical/onboarding/previews/{token}/-1/source").status_code == 404


def test_preview_detects_changes_after_preflight(tmp_path: Path) -> None:
    client, request, source_dir = _prepare(tmp_path)
    result = client.post("/historical/onboarding/preflight", json=request).json()
    token = result["preview_token"]
    assert token
    path = source_dir / "design0_front.png"
    Image.new("RGB", (128, 128), (100, 10, 30)).save(path)
    changed = client.get(f"/historical/onboarding/previews/{token}/0/source")
    assert changed.status_code == 404
    assert client.get("/datasets").json() == []


def test_blocked_preflight_still_supports_visual_inspection(tmp_path: Path) -> None:
    client, request, _ = _prepare(tmp_path)
    result = client.post(
        "/historical/onboarding/preflight",
        json={**request, "minimum_golden_cases": 100},
    ).json()
    assert not result["report"]["ready_to_import"]
    assert result["snapshot_id"] is None
    assert result["preview_token"] is not None
    token = result["preview_token"]
    assert client.get(f"/historical/onboarding/previews/{token}/0/target").status_code == 200


def test_preview_session_expires_and_rejects_stale_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pod_artwork_engine.historical_onboarding import HistoricalOnboardingBuilder
    _, request, _ = _prepare(tmp_path)
    report = HistoricalOnboardingBuilder().build_folders(
        Path(request["source_dir"]), Path(request["target_dir"]), minimum_golden_cases=1,
    )
    fake_clock = [1000.0]
    monkeypatch.setattr("pod_artwork_engine.pair_previews.time.monotonic", lambda: fake_clock[0])
    sessions = PairPreviewSessions(ttl_seconds=10)
    token = sessions.register(report)
    assert token
    assert sessions.image(token, 0, "target")
    fake_clock[0] = 1011.0
    with pytest.raises(LookupError, match="expired"):
        sessions.image(token, 0, "target")
