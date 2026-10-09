from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pod_artwork_engine.api import create_app
from pod_artwork_engine.checkpoints import CheckpointManager
from pod_artwork_engine.contracts import (
    ArtworkType, DesignSpec, JobRecord, JobState,
    MaterialSeparationDisposition, MaterialSeparationEvidence,
    QualityMode, RouteKind,
)
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.job_store import JobStore
from pod_artwork_engine.settings import Settings


def test_provider_config_persists_without_api_token(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(data_root=tmp_path / "data")
    client = TestClient(create_app(settings))
    config = {
        "url": "https://provider.example/execute",
        "name": "example",
        "model_alias": "image-model",
        "timeout_seconds": 80,
        "token": "very-private-api-key"
    }
    response = client.post("/provider/config", json=config)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["token_set"] is True
    assert "very-private-api-key" not in response.text
    path = settings.data_root / "provider-config.json"
    assert path.is_file()
    assert "very-private-api-key" not in path.read_text(encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["model_alias"] == "image-model"

    monkeypatch.setenv("POD_ARTWORK_DATA", str(settings.data_root))
    restarted = Settings.from_env()
    assert restarted.remote_provider_url == config["url"]
    assert restarted.remote_provider_model_alias == "image-model"
    assert restarted.remote_provider_token == ""
    assert data["provider_state"]["configured"] is True
    assert client.get("/provider/config").json()["token_set"] is True

    cleared = client.post("/provider/config", json={
        "url": config["url"], "name": config["name"],
        "model_alias": config["model_alias"], "timeout_seconds": 80,
        "clear_token": True
    })
    assert cleared.status_code == 200
    assert cleared.json()["token_set"] is False


def test_provider_config_validation_blocks_insecure_remote_transport(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(data_root=tmp_path / "data")))
    for value in ("http://provider.example/execute", "file:///etc/passwd", "https://username:password@provider.example"):
        response = client.post("/provider/config", json={
            "url": value, "name": "test", "timeout_seconds": 60
        })
        assert response.status_code == 400
    assert not (tmp_path / "data" / "provider-config.json").exists()


def test_retry_ai_requires_provider_and_preserves_parent_job(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(data_root=tmp_path / "data")
    settings.ensure_directories()
    source = tmp_path / "source.jpg"
    Image.new("RGB", (100, 100), (230, 230, 230)).save(source)
    parent = JobRecord(
        state=JobState.REVIEW_REQUIRED,
        quality_mode=QualityMode.PRINT_READY,
        source_paths=[str(source)],
        failure_reason="artwork_not_isolated_from_product_mockup",
    )
    JobStore(settings.database_path).save(parent)
    # Avoid executing a remote task during the API contract test.
    monkeypatch.setattr(Engine, "run_job", lambda self, job_id: self.jobs.get(job_id))
    client = TestClient(create_app(settings))
    blocked = client.post(f"/jobs/{parent.job_id}/retry-ai")
    assert blocked.status_code == 409

    configured = client.post("/provider/config", json={
        "url": "https://provider.example/execute",
        "name": "test",
        "model_alias": "image-model"
    })
    assert configured.status_code == 200
    retried = client.post(f"/jobs/{parent.job_id}/retry-ai")
    assert retried.status_code == 200, retried.text
    result = retried.json()
    assert result["job_id"] != parent.job_id
    assert result["parent_job_id"] == parent.job_id
    assert result["force_remote"] is True
    assert len(result["source_paths"]) == 1
    assert Path(result["source_paths"][0]).is_file()
    previous = client.get(f"/jobs/{parent.job_id}").json()
    assert previous["failure_reason"] == "artwork_not_isolated_from_product_mockup"


def test_forced_ai_route_does_not_use_deterministic_fallback(tmp_path: Path) -> None:
    engine = Engine(Settings(data_root=tmp_path / "data", remote_provider_url="https://provider.example/execute"))
    job = JobRecord(force_remote=True)
    spec = DesignSpec(artwork_type=ArtworkType.ILLUSTRATION, required_capabilities=["need_semantic_reconstruction"])
    route = engine._route(job, spec)
    assert route.route == RouteKind.REMOTE_SEMANTIC
    assert route.use_remote_provider
    assert not route.deterministic_finish
    assert "explicit_user_ai_retry" in route.reason_codes


def test_draft_preview_marked_and_not_exportable(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")
    settings.ensure_directories()
    job = JobRecord(
        state=JobState.REVIEW_REQUIRED,
        failure_reason="artwork_not_isolated_from_product_mockup, semantic_provider_not_used",
    )
    store = JobStore(settings.database_path)
    store.save(job)
    job_dir = settings.jobs_dir / job.job_id
    candidate = job_dir / "temp" / "candidate.png"
    candidate.parent.mkdir(parents=True)
    Image.new("RGBA", (700, 700), (100, 110, 120, 255)).save(candidate)
    CheckpointManager(settings.jobs_dir).write(job.job_id, "candidate", {
        "path": str(candidate), "local_baseline": True
    })
    client = TestClient(create_app(settings))
    draft = client.get(f"/jobs/{job.job_id}/draft")
    assert draft.status_code == 200, draft.text
    assert draft.headers["x-pod-output-type"] == "draft-not-print-ready"
    assert len(draft.content) > 100
    assert (job_dir / "preview" / "local-crop.png").is_file()
    assert client.get(f"/jobs/{job.job_id}/output").status_code in (404, 409)
    assert not (job_dir / "final" / "4500x5400.png").exists()
    assert client.get(f"/jobs/{job.job_id}/draft?original=true").status_code == 200

def test_provider_test_validates_typed_analyze_response(tmp_path: Path, monkeypatch) -> None:
    from pod_artwork_engine.contracts import ProviderAction, ProviderResult
    from pod_artwork_engine.providers import RemoteProvider

    seen = []
    def fake_execute(self, request):
        seen.append(request)
        return ProviderResult(
            provider="fake-compatible",
            model_version="test-v1",
            design_spec=DesignSpec(artwork_type=ArtworkType.ILLUSTRATION),
        )
    monkeypatch.setattr(RemoteProvider, "execute", fake_execute)

    client = TestClient(create_app(Settings(data_root=tmp_path / "data")))
    assert client.post("/provider/config", json={
        "url": "https://provider.example/execute",
        "name": "fake-compatible", "model_alias": "fake-model",
        "timeout_seconds": 70
    }).status_code == 200
    result = client.post("/provider/test")
    assert result.status_code == 200, result.text
    assert result.json()["analyze_contract_valid"] is True
    assert result.json()["reconstruct_contract_tested"] is False
    assert len(seen) == 1 and seen[0].action is ProviderAction.ANALYZE
    assert Path(seen[0].source_paths[0]).is_file()

def test_ai_candidate_preview_does_not_unlock_print_export(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")
    settings.ensure_directories()
    job = JobRecord(
        state=JobState.REVIEW_REQUIRED,
        force_remote=True,
        failure_reason="semantic_provider_not_used",
    )
    JobStore(settings.database_path).save(job)
    folder = settings.jobs_dir / job.job_id
    candidate = folder / "temp" / "candidate.png"
    candidate.parent.mkdir(parents=True)
    Image.new("RGBA", (700, 700), (15, 25, 90, 0)).save(candidate)
    CheckpointManager(settings.jobs_dir).write(job.job_id, "candidate", {
        "path": str(candidate), "local_baseline": False, "used_remote": True
    })
    client = TestClient(create_app(settings))
    info = client.get(f"/jobs/{job.job_id}/preview-info")
    assert info.status_code == 200
    assert info.json()["kind"] == "ai_candidate"
    assert info.json()["print_ready"] is False
    response = client.get(f"/jobs/{job.job_id}/ai-candidate")
    assert response.status_code == 200, response.text
    assert response.headers["x-pod-output-type"] == "ai-candidate-review-only"
    assert len(response.content) > 100
    assert client.get(f"/jobs/{job.job_id}/draft").status_code == 404
    assert client.get(f"/jobs/{job.job_id}/output").status_code == 409
