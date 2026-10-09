from __future__ import annotations

import io
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from pod_artwork_engine.api import create_app
from pod_artwork_engine.image_chat import ImageChatService, ImageChatConfig, ImageChatRequest, ImageChatError
from pod_artwork_engine.settings import Settings


def make_png(color: tuple[int, int, int] = (20, 120, 230)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (160, 120), (*color, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_chat_config_is_session_only(tmp_path: Path):
    settings = Settings(data_root=tmp_path)
    service = ImageChatService(settings)
    assert not service.status()["configured"]
    assert service.configure(ImageChatConfig(api_key="sk-test-only", model="gpt-image-1"))["configured"]
    assert "sk-test-only" not in str(list(tmp_path.rglob("*")))
    assert not (tmp_path / "image-chat-config.json").exists()
    assert service.configure(ImageChatConfig(clear_key=True))["configured"] is False


def test_chat_api_versions_without_external_provider(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("POD_IMAGE_API_KEY", raising=False)
    calls = []

    def fake_edit(self, key: str, model: str, prompt: str, image: list[bytes]):
        calls.append((key, model, prompt, len(image), Image.open(io.BytesIO(image[0])).size))
        return make_png((230, 80, 30))

    monkeypatch.setattr(ImageChatService, "_request_edit", fake_edit)
    with TestClient(create_app(Settings(data_root=tmp_path))) as client:
        assert client.get("/image-chat/config").json()["configured"] is False
        upload = client.post("/image-chat/sessions", files=[
            ("files", ("reference.png", make_png(), "image/png")),
            ("files", ("reference2.png", make_png((12, 15, 20)), "image/png")),
        ])
        assert upload.status_code == 200, upload.text
        job_id = upload.json()["job_id"]
        assert upload.json()["state"] == "queued"
        assert client.get(f"/jobs/{job_id}/image-chat/source").status_code == 200
        assert client.get(f"/jobs/{job_id}/output").status_code == 409
        assert client.post(f"/jobs/{job_id}/image-chat", json={"prompt":"Change the artwork color"}).status_code == 409
        assert client.post("/image-chat/config", json={
            "api_key":"sk-test-only", "model":"gpt-image-1"
        }).json()["configured"]
        assert client.post("/image-chat/config", json={
            "model":"../../bad"
        }).status_code == 400

        def edit(prompt: str, base_version_id: str | None = None) -> dict:
            response = client.post(f"/jobs/{job_id}/image-chat", json={
                "prompt":prompt, "base_version_id":base_version_id
            })
            assert response.status_code == 202, response.text
            version_id = response.json()["version_id"]
            for _ in range(100):
                versions = client.get(f"/jobs/{job_id}/image-chat").json()
                item = next(v for v in versions if v["version_id"] == version_id)
                if item["state"] != "running":
                    return item
                time.sleep(.02)
            raise AssertionError("Background AI edit did not finish")

        first = edit("Change blue to orange")
        assert first["state"] == "completed"
        second = edit("Add a small star to the corner", first["version_id"])
        assert second["state"] == "completed"
        assert second["base_version_id"] == first["version_id"]
        assert len(client.get(f"/jobs/{job_id}/image-chat").json()) == 2
        assert calls[0][:3] == ("sk-test-only", "gpt-image-1", "Change blue to orange")
        assert calls[0][3] == 2  # Multiple references on the initial edit.
        assert calls[1][2] == "Add a small star to the corner"
        assert calls[1][3] == 1  # Later edit uses only the chosen version.
        for version in (first, second):
            image = client.get(f"/jobs/{job_id}/image-chat/{version['version_id']}/image")
            assert image.status_code == 200
            assert image.headers["x-pod-output-type"] == "ai-edit-preview-not-print-ready"
            assert Image.open(io.BytesIO(image.content)).format == "PNG"
        assert client.get(f"/jobs/{job_id}/output").status_code == 409
        assert client.post(f"/jobs/{job_id}/image-chat", json={
            "prompt": "Another edit", "base_version_id":"0" * 32
        }).status_code == 409
        assert client.get(f"/jobs/{job_id}/image-chat/{'0'*32}/image").status_code == 404
        assert not any("sk-test-only" in p.read_text(encoding="utf8") for p in (
            tmp_path / "jobs" / job_id / "image-chat"
        ).glob("*.json"))


def test_chat_invalid_image_and_missing_job(tmp_path: Path):
    with TestClient(create_app(Settings(data_root=tmp_path))) as client:
        assert client.post("/image-chat/sessions", files=[
            ("files", ("invalid.png", b"not an image", "image/png"))
        ]).status_code == 400
        assert client.get("/jobs/no-such-id/image-chat").status_code == 404
        assert client.post("/jobs/no-such-id/image-chat", json={
            "prompt":"Recolor the artwork"
        }).status_code == 404
        assert client.post("/image-chat/config", json={"model":"invalid/path"}).status_code == 400
