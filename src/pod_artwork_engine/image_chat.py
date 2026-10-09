"""Multi-turn AI image editing with immutable, job-scoped versions.

Generated images are previews until the normal POD quality gates approve them.
The API key is engine-session only; image edits use the official OpenAI Images API.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from .checkpoints import CheckpointManager
from .contracts import JobRecord, JobState
from .settings import Settings
from .storage import StorageManager, StorageLimitExceeded

_IMAGE_ENDPOINT = "https://api.openai.com/v1/images/edits"
_MODEL_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{1,80}$")


class ImageChatRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    base_version_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class ImageChatConfig(BaseModel):
    api_key: str | None = Field(default=None, max_length=512)
    model: str = Field(default="gpt-image-1", max_length=80)
    clear_key: bool = False


class ImageChatError(Exception):
    pass


class ImageChatService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.storage = StorageManager(settings)
        self._key = os.environ.get("POD_IMAGE_API_KEY", "").strip()
        self._model = os.environ.get("POD_IMAGE_MODEL", "gpt-image-1")
        self._lock = threading.Lock()
        self._active_jobs: set[str] = set()

    def status(self) -> dict:
        with self._lock:
            return {"configured": bool(self._key), "model": self._model,
                    "key_persistence": "session_only_or_environment",
                    "provider": "OpenAI Images API"}

    def configure(self, request: ImageChatConfig) -> dict:
        if not _MODEL_PATTERN.fullmatch(request.model):
            raise ImageChatError("Invalid image model identifier")
        with self._lock:
            self._model = request.model
            if request.clear_key:
                self._key = ""
            elif request.api_key is not None and request.api_key.strip():
                self._key = request.api_key.strip()
        return self.status()

    def _folder(self, job_id: str) -> Path:
        # The ID is sourced from an existing job, never from an arbitrary path.
        return self.settings.jobs_dir / job_id / "image-chat"

    def list_versions(self, job_id: str) -> list[dict]:
        folder = self._folder(job_id)
        if not folder.is_dir():
            return []
        entries = []
        for path in folder.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("version_id") == path.stem:
                    # Recover interrupted edits without losing completed versions.
                    with self._lock:
                        interrupted = record.get("state") == "running" and job_id not in self._active_jobs
                    if interrupted:
                        record["state"] = "failed"
                        record["error"] = "Engine restarted during this edit. Send the request again."
                        self._save(job_id, record)
                    entries.append(record)
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(entries, key=lambda row: row["created_at"])

    def get_version(self, job_id: str, version_id: str) -> dict | None:
        if not re.fullmatch(r"[0-9a-f]{32}", version_id):
            return None
        path = self._folder(job_id) / f"{version_id}.json"
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            return record if record.get("version_id") == version_id else None
        except (OSError, json.JSONDecodeError):
            return None

    def image_path(self, job_id: str, version_id: str) -> Path | None:
        version = self.get_version(job_id, version_id)
        path = self._folder(job_id) / f"{version_id}.png"
        return path if version and version["state"] == "completed" and path.is_file() else None

    def _save(self, job_id: str, version: dict) -> None:
        folder = self._folder(job_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{version['version_id']}.json"
        temp = folder / f".{version['version_id']}.tmp"
        temp.write_text(json.dumps(version, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)

    def start(self, job: JobRecord, request: ImageChatRequest) -> dict:
        if len(request.prompt.strip()) < 3:
            raise ImageChatError("Describe the edit with at least 3 characters")
        with self._lock:
            if not self._key:
                raise ImageChatError("Set the OpenAI image API key before editing")
            if job.job_id in self._active_jobs:
                raise ImageChatError("An image edit for this job is already running")
            source = self._source(job, request.base_version_id)
            if not source or not source.is_file():
                raise ImageChatError("No source image is available for editing")
            version = {
                "version_id": uuid4().hex,
                "base_version_id": request.base_version_id,
                "prompt": request.prompt.strip(),
                "state": "running",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "model": self._model,
                "error": None,
                "print_ready": False,
            }
            self._save(job.job_id, version)
            self._active_jobs.add(job.job_id)
        return version

    def source_for_job(self, job: JobRecord) -> Path | None:
        return self._source(job, None)

    def _source(self, job: JobRecord, base_version_id: str | None) -> Path | None:
        if base_version_id:
            return self.image_path(job.job_id, base_version_id)
        if job.state is JobState.COMPLETED and job.result_path:
            path = Path(job.result_path)
            if path.is_file():
                return path
        if job.state is JobState.REVIEW_REQUIRED:
            checkpoint = CheckpointManager(self.settings.jobs_dir).payload(job.job_id, "candidate")
            if isinstance(checkpoint, dict):
                path = Path(str(checkpoint.get("path", ""))).resolve()
                root = (self.settings.jobs_dir / job.job_id / "temp").resolve()
                if path.is_file() and path.is_relative_to(root):
                    return path
        if job.source_paths:
            path = Path(job.source_paths[0]).resolve()
            root = (self.settings.jobs_dir / job.job_id / "source").resolve()
            if path.is_relative_to(root) and path.is_file():
                return path
        return None

    def generate(self, job: JobRecord, version_id: str) -> None:
        version = self.get_version(job.job_id, version_id)
        if version is None:
            return
        try:
            with self._lock:
                key = self._key
            if not key:
                raise ImageChatError("Image API key was cleared")
            source = self._source(job, version["base_version_id"])
            if source is None:
                raise ImageChatError("Image source is no longer available")
            # A fresh session uses the first image as editing target and
            # remaining images as visual references. Subsequent turns edit the
            # chosen immutable version, not the original files.
            paths = [source]
            if version["base_version_id"] is None and job.state is JobState.QUEUED:
                root = (self.settings.jobs_dir / job.job_id / "source").resolve()
                paths.extend(
                    candidate for name in job.source_paths[1:10]
                    if (candidate := Path(name).resolve()).is_file()
                    and candidate.is_relative_to(root)
                )
            images = []
            for path in paths:
                with Image.open(path) as original:
                    image = ImageOps.exif_transpose(original).convert("RGBA")
                    image.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    images.append(buffer.getvalue())
            payload = self._request_edit(key, version["model"], version["prompt"], images)
            self.settings.ensure_directories()
            self._save_image(job.job_id, version_id, payload)
            version["state"] = "completed"
        except (OSError, ValueError, ImageChatError, StorageLimitExceeded, urllib.error.URLError) as exc:
            version["state"] = "failed"
            # Do not propagate potentially sensitive HTTP error bodies to UI/logs.
            version["error"] = (str(exc)[:240] if isinstance(exc, (ImageChatError, StorageLimitExceeded))
                                else "Image provider unavailable or returned invalid image")
        finally:
            self._save(job.job_id, version)
            with self._lock:
                self._active_jobs.discard(job.job_id)

    def _request_edit(self, key: str, model: str, prompt: str, images: list[bytes]) -> bytes:
        boundary = "pod" + uuid4().hex
        def field(name: str, value: str) -> bytes:
            return (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode()
        parts = [
            field("model", model), field("prompt", prompt),
            field("size", "auto"), field("quality", "high"),
        ]
        for index, image in enumerate(images):
            name = "image" if len(images) == 1 else "image[]"
            parts.extend([
                (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"reference-{index}.png\"\r\nContent-Type: image/png\r\n\r\n").encode(),
                image, b"\r\n",
            ])
        body = b"".join([*parts, f"--{boundary}--\r\n".encode()])
        req = urllib.request.Request(
            _IMAGE_ENDPOINT, data=body, method="POST",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=240) as response:
                if response.status != 200:
                    raise ImageChatError(f"Image provider returned HTTP {response.status}")
                raw = response.read(40 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            raise ImageChatError(f"Image provider returned HTTP {exc.code}; check key, model and billing") from None
        if len(raw) > 40 * 1024 * 1024:
            raise ImageChatError("Image provider response exceeded size limit")
        try:
            data = json.loads(raw)
            result = base64.b64decode(data["data"][0]["b64_json"], validate=True)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
            raise ImageChatError("Image provider did not return an image") from exc
        if len(result) > 25 * 1024 * 1024:
            raise ImageChatError("Generated image exceeded size limit")
        return result

    def _save_image(self, job_id: str, version_id: str, raw: bytes) -> None:
        self.storage.assert_capacity(len(raw))
        folder = self._folder(job_id)
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"{version_id}.png"
        temp = folder / f".{version_id}.png.tmp"
        try:
            with Image.open(io.BytesIO(raw)) as result:
                result.verify()
            with Image.open(io.BytesIO(raw)) as result:
                result.convert("RGBA").save(temp, format="PNG")
            os.replace(temp, destination)
        finally:
            temp.unlink(missing_ok=True)
