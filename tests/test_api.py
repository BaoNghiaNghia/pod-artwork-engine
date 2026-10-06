import time
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pod_artwork_engine.api import create_app
from pod_artwork_engine.dataset_registry import DatasetRegistry
from pod_artwork_engine.harness import HarnessStore
from pod_artwork_engine.harness_models import BenchmarkScorecard, BenchmarkTier, HarnessRunStatus
from pod_artwork_engine.settings import Settings, StorageLimits


def make_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (64, 64), (255, 0, 0, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_health_and_upload_preflight(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path,
        storage=StorageLimits(
            soft_total_bytes=100 * 1024 * 1024,
            hard_total_bytes=200 * 1024 * 1024,
            cache_bytes=20 * 1024 * 1024,
            temp_jobs_bytes=20 * 1024 * 1024,
            logs_bytes=20 * 1024 * 1024,
            updates_bytes=20 * 1024 * 1024,
        ),
    )
    client = TestClient(create_app(settings))

    assert client.get("/health").json()["status"] == "ok"
    status = client.get("/status").json()
    assert "update" in status
    assert "local_ocr_available" in status
    assert "local_ocr_enabled" in status
    assert "local_ocr_backend" in status
    assert "source" in status["local_ocr_backend"]
    assert "version" in status["local_ocr_backend"]
    assert "path" not in status["local_ocr_backend"]
    assert "executable" not in status["local_ocr_backend"]
    assert status["visual_font_match_enabled"] is True
    assert status["reference_fusion_method"] == "deterministic_reference_fusion_v1"
    assert status["reference_alignment_method"] == "geometric_reference_alignment_v1"
    assert status["feature_correspondence_method"] == "feature_correspondence_benchmark_v1"
    assert status["registration_calibration_method"] == "golden_registration_calibration_v1"
    assert status["dewarp_benchmark_method"] == "benchmark_dewarp_registration_v1"
    assert status["dewarp_experiment_method"] == "golden_dewarp_experiment_v1"
    assert (
        status["production_registration_policy_method"]
        == "production_registration_policy_proposal_v1"
    )
    assert status["region_confidence_method"] == "normalized_region_evidence_v1"
    assert status["region_rescue_planner_method"] == "region_rescue_plan_v1"
    assert status["representation_planner_method"] == "representation_plan_v1"
    assert status["material_separation_method"] == "material_separation_evidence_v1"
    assert status["texture_handling_method"] == "texture_handling_evidence_v1"
    assert status["super_resolution_method"] == "super_resolution_readiness_v1"
    assert status["sr_benchmark_method"] == "sr_benchmark_matrix_v1"
    assert status["sr_adapter_method"] == "sr_adapter_materializer_v1"
    assert status["sr_cohort_method"] == "sr_fair_cohort_v1"
    assert status["sr_experiment_method"] == "sr_golden_experiment_v1"
    assert status["visual_font_match_min_score"] == 0.72
    assert status["visual_font_match_min_margin"] == 0.035
    assert status["geometry_path_capabilities"] == [
        "cubic",
        "multi_subpath",
        "evenodd_compound_fill",
    ]
    assert status["qc_policy_id"] == "phase1-default"
    assert status["qc_policy_version"] == "1"
    assert status["qc_policy_configured"] is False
    assert status["router_policy_id"] == "phase1-router-default"
    assert status["router_policy_version"] == "1"
    assert status["router_policy_configured"] is False

    response = client.post(
        "/jobs?quality_mode=print_ready",
        files={"files": ("sample.png", make_png(), "image/png")},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    deadline = time.time() + 5
    job = {}
    terminal = {"completed", "review_required", "failed_final", "cancelled"}
    while time.time() < deadline:
        job = client.get(f"/jobs/{job_id}").json()
        if job["state"] in terminal:
            break
        time.sleep(0.05)

    assert job["state"] in {"completed", "review_required"}
    checkpoint = tmp_path / "jobs" / job_id / "checkpoints" / "preflight.json"
    assert checkpoint.exists()
    assert job["result_path"]
    output = client.get(f"/jobs/{job_id}/output")
    assert output.status_code == 200
    assert output.headers["content-type"].startswith("image/png")


def test_dataset_read_api(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path)
    source = tmp_path / "source.png"
    target = tmp_path / "target.png"
    Image.new("RGB", (32, 32), (20, 30, 40)).save(source)
    Image.new("RGB", (32, 32), (180, 70, 30)).save(target)

    registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
    registry.register_pair("design-api", [source], target)
    dataset = registry.create_dataset("historical-api")

    client = TestClient(create_app(settings))

    datasets = client.get("/datasets")
    assert datasets.status_code == 200
    assert any(item["dataset_id"] == dataset.dataset_id for item in datasets.json())

    members = client.get(f"/datasets/{dataset.dataset_id}/members")
    assert members.status_code == 200
    assert len(members.json()) == 1

    pairs = client.get("/historical/pairs")
    assert pairs.status_code == 200
    assert pairs.json()[0]["pair_key"] == "design-api"

    status = client.get("/status").json()
    assert status["dataset_count"] == 1
    assert status["historical_pair_count"] == 1


def test_harness_read_api(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path)
    store = HarnessStore(settings.harness_dir)
    scorecard = BenchmarkScorecard(
        run_id="run-api",
        dataset_id="dataset-api",
        tier=BenchmarkTier.SMOKE,
        recipe_id="recipe-api",
        recipe_version="1",
        status=HarnessRunStatus.COMPLETE,
        case_count=1,
        success_count=1,
        failure_count=0,
        manual_review_count=0,
        quality_mean=0.9,
    )
    store.save_model(store.scorecard_path(scorecard.run_id), scorecard)

    client = TestClient(create_app(settings))

    response = client.get("/harness/runs")
    assert response.status_code == 200
    assert response.json()[0]["run_id"] == "run-api"

    detail = client.get("/harness/runs/run-api")
    assert detail.status_code == 200
    assert detail.json()["scorecard_id"] == scorecard.scorecard_id

    status = client.get("/status").json()
    assert status["harness_run_count"] == 1
