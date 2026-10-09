from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field


class ProviderConfiguration(BaseModel):
    url: str = Field(default="", max_length=2048)
    name: str = Field(default="remote", min_length=1, max_length=80)
    model_alias: str = Field(default="", max_length=160)
    timeout_seconds: float = Field(default=120.0, ge=5, le=600)


def _validate_endpoint(url: str) -> None:
    if not url:
        return
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Provider URL must be a valid HTTPS address")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Provider URL must not contain credentials or fragments")
    # No unauthenticated plain-HTTP transport to remote hosts.
    if parsed.scheme == "http" and parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Plain HTTP is only allowed for localhost providers")
    if parsed.port is not None and not (1 <= parsed.port <= 65535):
        raise ValueError("Invalid provider port")


def configuration_path(data_root: Path) -> Path:
    return data_root / "provider-config.json"


def load_configuration(data_root: Path) -> ProviderConfiguration | None:
    path = configuration_path(data_root)
    if not path.is_file():
        return None
    config = ProviderConfiguration.model_validate_json(path.read_text(encoding="utf-8"))
    _validate_endpoint(config.url)
    return config


def save_configuration(data_root: Path, config: ProviderConfiguration) -> None:
    _validate_endpoint(config.url)
    data_root.mkdir(parents=True, exist_ok=True)
    target = configuration_path(data_root)
    # A provider configuration holds NO tokens. API keys stay session-only or
    # can be injected via POD_REMOTE_PROVIDER_TOKEN on engine startup.
    fd, temp_path = tempfile.mkstemp(prefix=".provider-", suffix=".tmp", dir=data_root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config.model_dump(), handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    finally:
        Path(temp_path).unlink(missing_ok=True)
