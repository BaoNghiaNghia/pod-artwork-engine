from __future__ import annotations

import base64
import binascii
import io
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

from .contracts import ProviderAction, ProviderActionRecipe, ProviderRecipe, ProviderRequest, ProviderResult
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
        self._failure_count = 0
        self._circuit_open_until = 0.0
        self._state_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.settings.remote_provider_url.strip())

    @property
    def available(self) -> bool:
        if not self.configured:
            return False
        with self._state_lock:
            return time.monotonic() >= self._circuit_open_until

    def status(self) -> dict[str, int | float | bool]:
        with self._state_lock:
            remaining = max(0.0, self._circuit_open_until - time.monotonic())
            failures = self._failure_count
        return {
            "configured": self.configured,
            "available": self.configured and remaining <= 0,
            "failure_count": failures,
            "cooldown_remaining_seconds": round(remaining, 3),
        }

    def _before_request(self) -> None:
        if not self.configured:
            raise ProviderUnavailable("remote provider URL is not configured")
        with self._state_lock:
            remaining = self._circuit_open_until - time.monotonic()
        if remaining > 0:
            raise ProviderUnavailable(
                f"remote provider circuit open for {remaining:.1f}s"
            )

    def _record_success(self) -> None:
        with self._state_lock:
            self._failure_count = 0
            self._circuit_open_until = 0.0

    def _record_failure(self) -> None:
        with self._state_lock:
            self._failure_count += 1
            if self._failure_count >= self.settings.remote_provider_failure_threshold:
                self._circuit_open_until = (
                    time.monotonic() + self.settings.remote_provider_cooldown_seconds
                )

    def recipe(self) -> ProviderRecipe:
        path = self.settings.provider_recipe_path
        if path is None:
            return ProviderRecipe(
                provider_name=self.settings.remote_provider_name,
                actions=[ProviderActionRecipe(action=action, model_alias=self.settings.remote_provider_model_alias) for action in ProviderAction],
            )
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise ProviderProtocolError(f"provider recipe not found: {resolved}")
        try:
            return validate_typed_payload(
                ProviderRecipe,
                resolved.read_text(encoding="utf-8"),
            )
        except (OSError, TypedBoundaryError) as exc:
            raise ProviderProtocolError(f"invalid provider recipe: {exc}") from exc

    def _action_recipe(self, request: ProviderRequest) -> tuple[ProviderRecipe, ProviderActionRecipe]:
        recipe = self.recipe()
        action_recipe = recipe.for_action(request.action)
        if action_recipe is None:
            if request.action.value == "super_resolution":
                raise ProviderUnavailable(
                    "provider super_resolution action is not explicitly configured"
                )
            action_recipe = ProviderActionRecipe(action=request.action)
        if not action_recipe.enabled:
            raise ProviderUnavailable(
                f"provider action disabled by recipe: {request.action.value}"
            )
        return recipe, action_recipe

    def execute(self, request: ProviderRequest) -> ProviderResult:
        self._before_request()

        recipe, action_recipe = self._action_recipe(request)
        payload = request.model_dump(mode="json")
        payload["provider_recipe"] = {
            "recipe_id": recipe.recipe_id,
            "version": recipe.version,
            "provider_name": recipe.provider_name,
            "model_alias": action_recipe.model_alias,
            "parameters": action_recipe.parameters,
        }
        # Local filesystem paths are not sent to remote providers.
        payload["source_paths"] = [Path(path).name for path in request.source_paths]
        payload["images"] = [
            _encode_reference(
                Path(path),
                long_edge=action_recipe.max_reference_long_edge,
            )
            for path in request.source_paths
        ]
        if request.candidate_path:
            candidate = Path(request.candidate_path)
            payload["candidate_path"] = candidate.name
            payload["candidate_image"] = _encode_reference(
                candidate,
                long_edge=action_recipe.max_reference_long_edge,
            )
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
        timeout = (
            action_recipe.timeout_seconds
            if action_recipe.timeout_seconds is not None
            else self.settings.remote_provider_timeout_seconds
        )
        try:
            with urllib.request.urlopen(
                http_request,
                timeout=timeout,
            ) as response:
                raw = response.read()
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            self._record_failure()
            raise ProviderUnavailable(f"remote provider request failed: {exc}") from exc

        try:
            result = validate_typed_payload(ProviderResult, raw)
        except TypedBoundaryError as exc:
            self._record_failure()
            raise ProviderProtocolError(str(exc)) from exc

        self._record_success()
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
