from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pydantic import BaseModel, Field
from PIL import Image

from .contracts import ProviderAction, ProviderRequest, QualityMode
from .provider_config import ProviderConfiguration, save_configuration
from .providers import ProviderUnavailable, ProviderProtocolError, RemoteProvider
from .settings import Settings


class ProviderConfigUpdate(ProviderConfiguration):
    token: str | None = Field(default=None, repr=False, max_length=4096)
    clear_token: bool = False


def sanitized_config(provider: RemoteProvider) -> dict:
    settings = provider.settings
    return {
        "url": settings.remote_provider_url,
        "name": settings.remote_provider_name,
        "model_alias": settings.remote_provider_model_alias,
        "timeout_seconds": settings.remote_provider_timeout_seconds,
        "token_set": bool(settings.remote_provider_token),
        "token_persistence": "session_only_or_environment",
        "provider_state": provider.status(),
        "protocol": "POD typed ProviderRequest/ProviderResult, not a direct OpenAI URL",
    }


def apply_provider_config(provider: RemoteProvider, request: ProviderConfigUpdate) -> RemoteProvider:
    config = ProviderConfiguration.model_validate(request.model_dump(exclude={"token", "clear_token"}))
    current = provider.settings
    token = "" if request.clear_token else (
        request.token if request.token is not None else current.remote_provider_token
    )
    updated = replace(
        current,
        remote_provider_url=config.url.strip(),
        remote_provider_name=config.name.strip(),
        remote_provider_model_alias=config.model_alias.strip(),
        remote_provider_timeout_seconds=config.timeout_seconds,
        remote_provider_token=token,
    )
    # Persist only non-sensitive metadata; never write the access token to disk.
    save_configuration(current.data_root, config)
    return RemoteProvider(updated)


def test_provider_contract(provider: RemoteProvider) -> dict:
    if not provider.available:
        raise ProviderUnavailable("Configure an available POD-compatible provider first")
    root = provider.settings.data_root / "cache" / "provider-test"
    root.mkdir(parents=True, exist_ok=True)
    probe = root / "probe.png"
    if not probe.exists():
        Image.new("RGB", (512, 512), (243, 243, 243)).save(probe)
    result = provider.execute(
        ProviderRequest(
            action=ProviderAction.ANALYZE,
            job_id="provider-connectivity-test",
            quality_mode=QualityMode.QUICK_2D,
            source_paths=[str(probe)],
            requested_capabilities=["need_design_spec"],
        )
    )
    if result.design_spec is None:
        raise ProviderProtocolError("Provider responded without a typed design_spec")
    return {
        "connected": True,
        "analyze_contract_valid": True,
        "reconstruct_contract_tested": False,
        "provider": result.provider,
        "model_version": result.model_version,
        "note": "Reconstruction is verified only when an actual image job runs.",
    }
