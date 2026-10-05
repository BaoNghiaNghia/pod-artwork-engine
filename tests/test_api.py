import time
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pod_artwork_engine.api import create_app
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

    response = client.post(
        "/jobs?quality_mode=print_ready",
        files={"files": ("sample.png", make_png(), "image/png")},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    deadline = time.time() + 3
    job = {}
    while time.time() < deadline:
        job = client.get(f"/jobs/{job_id}").json()
        if job["state"] != "queued":
            break
        time.sleep(0.05)

    assert job["state"] in {"preflight", "waiting_provider"}
    checkpoint = tmp_path / "jobs" / job_id / "checkpoints" / "preflight.json"
    deadline = time.time() + 3
    while time.time() < deadline and not checkpoint.exists():
        time.sleep(0.05)

    assert checkpoint.exists()
