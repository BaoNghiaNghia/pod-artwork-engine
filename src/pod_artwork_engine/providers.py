from __future__ import annotations

import base64
import binascii
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

from .contracts import ProviderRequest, ProviderResult
from .settings import Settings
from .typed_control import TypedBoundaryError, validate_typed_payload


class ProviderUnavailable(RuntimeError):
    pass


class ProviderProtocolError(RuntimeError):
    pass


def _encode_reference(path: Path, long_edge: int = 1600) -> dict[str, str]:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        image.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        if "A" in image.getbands() and image.getchannel("A").getextrema() != (255, 255):
            image.save(buffer, format="PNG", optimize=True)
            media_type = "image/png"
        else:
            image.convert("RGB").save(buffer, format="JPEG", quality=90, optimize=True)
            media_type = "image/jpeg"
    return {
        "filename": path.name,
        "media_type": media_type,
        "base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
    }


class RemoteProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def available(self) -> bool:
        return bool(self.settings.remote_provider_url.strip())

    def execute(self, request: ProviderRequest) -> ProviderResult:
        if not self.available:
            raise ProviderUnavailable("remote provider URL is not configured")

        payload = request.model_dump(mode="json")
        # Local filesystem paths are not sent to remote providers.
        payload["source_paths"] = [Path(path).name for path in request.source_paths]
        payload["images"] = [
            _encode_reference(Path(path))
            for path in request.source_paths
        ]
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "PODArtworkEngine/1",
        }
        if self.settings.remote_provider_token:
            headers["Authorization"] = f"Bearer {self.settings.remote_provider_token}"

        http_request = urllib.request.Request(
            self.settings.remote_provider_url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                http_request,
                timeout=self.settings.remote_provider_timeout_seconds,
            ) as response:
                raw = response.read()
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise ProviderUnavailable(f"remote provider request failed: {exc}") from exc

        try:
            result = validate_typed_payload(ProviderResult, raw)
        except TypedBoundaryError as exc:
            raise ProviderProtocolError(str(exc)) from exc

        if not result.provider:
            result.provider = self.settings.remote_provider_name
        return result


def materialize_provider_candidate(
    result: ProviderResult,
    output_path: Path,
) -> Path | None:
    if result.candidate_path:
        candidate = Path(result.candidate_path).expanduser().resolve()
        allowed_root = output_path.parent.resolve()
        if candidate.is_file() and candidate.is_relative_to(allowed_root):
            return candidate

    encoded = result.candidate_image_base64
    if not encoded:
        return None
    if encoded.startswith("data:"):
        _, _, encoded = encoded.partition(",")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProviderProtocolError("provider candidate image is not valid base64") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    try:
        with Image.open(output_path) as image:
            image.verify()
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        raise ProviderProtocolError("provider candidate image is invalid") from exc
    return output_path
