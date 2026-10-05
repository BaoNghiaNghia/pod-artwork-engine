from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from pod_artwork_engine.contracts import ProviderAction, ProviderRequest, QualityMode
from pod_artwork_engine.providers import ProviderUnavailable, RemoteProvider
from pod_artwork_engine.settings import Settings


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def _image(path: Path) -> Path:
    Image.new("RGB", (900, 700), (120, 80, 40)).save(path)
    return path


def test_provider_recipe_enriches_gateway_payload_without_local_paths(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = _image(tmp_path / "private-reference.png")
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "recipe_id": "test-v1",
                "version": "7",
                "provider_name": "gateway",
                "actions": [
                    {
                        "action": "analyze",
                        "model_alias": "vision_reasoning",
                        "max_reference_long_edge": 1024,
                        "timeout_seconds": 33,
                        "parameters": {"quality": "balanced"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        data_root=tmp_path / "data",
        remote_provider_url="https://gateway.invalid/provider",
        remote_provider_name="gateway",
        provider_recipe_path=recipe_path,
    )
    provider = RemoteProvider(settings)
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response(
            json.dumps(
                {
                    "provider": "gateway",
                    "model_version": "fixture",
                }
            ).encode("utf-8")
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = provider.execute(
        ProviderRequest(
            action=ProviderAction.ANALYZE,
            job_id="job-fixture",
            quality_mode=QualityMode.PRINT_READY,
            source_paths=[str(source)],
            requested_capabilities=["need_design_spec"],
        )
    )

    assert result.provider == "gateway"
    assert captured["timeout"] == 33
    payload = captured["payload"]
    assert payload["source_paths"] == ["private-reference.png"]
    assert str(tmp_path) not in json.dumps(payload)
    assert payload["provider_recipe"] == {
        "recipe_id": "test-v1",
        "version": "7",
        "provider_name": "gateway",
        "model_alias": "vision_reasoning",
        "parameters": {"quality": "balanced"},
    }
    assert payload["images"][0]["filename"] == "private-reference.png"


def test_provider_recipe_can_disable_action(tmp_path: Path) -> None:
    source = _image(tmp_path / "reference.png")
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "recipe_id": "restricted",
                "actions": [
                    {
                        "action": "judge",
                        "enabled": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    provider = RemoteProvider(
        Settings(
            data_root=tmp_path / "data",
            remote_provider_url="https://gateway.invalid/provider",
            provider_recipe_path=recipe_path,
        )
    )

    with pytest.raises(ProviderUnavailable):
        provider.execute(
            ProviderRequest(
                action=ProviderAction.JUDGE,
                job_id="job-fixture",
                quality_mode=QualityMode.MAX_FIDELITY,
                source_paths=[str(source)],
            )
        )
