from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

from pydantic import BaseModel, ConfigDict, HttpUrl


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    channel: str
    package_url: HttpUrl
    sha256: str
    minimum_updater_version: str = "0.1.0"


def load_remote_manifest(url: str, timeout_seconds: int = 8) -> ReleaseManifest:
    request = urllib.request.Request(url, headers={"User-Agent": "PODArtworkTool-Updater/0.1"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return ReleaseManifest.model_validate(payload)


def verify_sha256(path: Path, expected_sha256: str) -> bool:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().lower() == expected_sha256.lower()
