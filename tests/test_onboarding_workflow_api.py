import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pod_artwork_engine.api import create_app
from pod_artwork_engine.settings import Settings


def _corpus(root: Path, count: int = 4) -> tuple[Path, Path]:
    sources = root / "sources"
    targets = root / "finals"
    sources.mkdir()
    targets.mkdir()
    for index in range(count):
        Image.new("RGB", (96, 96), (20 + index * 20, 40, 70)).save(
            sources / f"artwork{index}_front.png"
        )
        # Distinct aspect ratios keep unique design identities in the registry.
        Image.new("RGBA", (92 + index * 12, 96), (210, 20 + index * 40, 40, 255)).save(
            targets / f"artwork{index}_final.png"
        )
    return sources, targets


def test_preflight_is_read_only_and_import_is_explicit(tmp_path: Path) -> None:
    sources, targets = _corpus(tmp_path)
    client = TestClient(create_app(Settings(data_root=tmp_path / "app")))
    request = {
        "mode": "folders",
        "source_dir": str(sources),
        "target_dir": str(targets),
        "minimum_golden_cases": 1,
        "seed": "fixture-v1",
    }
    first = client.post("/historical/onboarding/preflight", json=request)
    assert first.status_code == 200
    payload = first.json()
    assert payload["report"]["ready_to_import"]
    assert payload["report"]["mutates_registry"] is False
    assert payload["production_execution_enabled"] is False
    assert payload["report"]["pair_count"] == 4
    assert payload["snapshot_id"]
    assert client.get("/datasets").json() == []
    assert client.get("/historical/pairs").json() == []

    # No silent mutation: confirmation is required at the API boundary.
    incomplete = client.post(
        "/historical/onboarding/import",
        json={**request, "snapshot_id": payload["snapshot_id"], "dataset_name": "fixture"},
    )
    assert incomplete.status_code == 422
    assert client.get("/datasets").json() == []

    imported = client.post(
        "/historical/onboarding/import",
        json={
            **request,
            "snapshot_id": payload["snapshot_id"],
            "dataset_name": "fixture",
            "confirmation": "IMPORT",
        },
    )
    assert imported.status_code == 200
    assert imported.json()["production_execution_enabled"] is False
    assert imported.json()["dataset"]["dataset_id"]
    assert len(imported.json()["imported_pair_ids"]) == 4
    assert len(client.get("/datasets").json()) == 1


def test_preflight_blocks_empty_corpus_and_import(tmp_path: Path) -> None:
    sources, targets = _corpus(tmp_path, count=0)
    client = TestClient(create_app(Settings(data_root=tmp_path / "app")))
    data = {
        "source_dir": str(sources),
        "target_dir": str(targets),
        "minimum_golden_cases": 1,
    }
    response = client.post("/historical/onboarding/preflight", json=data)
    assert response.status_code == 200
    assert response.json()["report"]["ready_to_import"] is False
    assert response.json()["snapshot_id"] is None
    blocked = client.post(
        "/historical/onboarding/import",
        json={**data, "snapshot_id": "0" * 64, "dataset_name": "must-not-import", "confirmation": "IMPORT"},
    )
    assert blocked.status_code == 409
    assert client.get("/datasets").json() == []


def test_import_rejects_modified_bytes_after_preflight(tmp_path: Path) -> None:
    sources, targets = _corpus(tmp_path)
    client = TestClient(create_app(Settings(data_root=tmp_path / "app")))
    request = {
        "mode": "folders",
        "source_dir": str(sources),
        "target_dir": str(targets),
        "minimum_golden_cases": 1,
    }
    preflight = client.post("/historical/onboarding/preflight", json=request).json()
    assert preflight["snapshot_id"]

    Image.new("RGB", (96, 96), (255, 120, 100)).save(sources / "artwork0_front.png")
    result = client.post(
        "/historical/onboarding/import",
        json={**request, "snapshot_id": preflight["snapshot_id"], "dataset_name": "stale", "confirmation": "IMPORT"},
    )
    assert result.status_code == 409
    assert "changed" in str(result.json()["detail"]).lower()
    assert client.get("/datasets").json() == []


def test_manifest_preflight_is_read_only(tmp_path: Path) -> None:
    sources, targets = _corpus(tmp_path)
    manifest = tmp_path / "pairs.json"
    records = [
        {"pair_key": f"artwork{i}", "sources": [str(sources / f"artwork{i}_front.png")], "target": str(targets / f"artwork{i}_final.png")}
        for i in range(4)
    ]
    manifest.write_text(json.dumps({"pairs": records}), encoding="utf-8")
    client = TestClient(create_app(Settings(data_root=tmp_path / "app")))
    preflight = client.post(
        "/historical/onboarding/preflight",
        json={"mode": "manifest", "manifest_path": str(manifest), "minimum_golden_cases": 1},
    )
    assert preflight.status_code == 200
    assert preflight.json()["report"]["pair_count"] == 4
    assert preflight.json()["snapshot_id"]
    assert client.get("/historical/pairs").json() == []
