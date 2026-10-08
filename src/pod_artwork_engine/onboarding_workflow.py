from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .historical_onboarding import HistoricalOnboardingBuilder, HistoricalOnboardingReport
from .preflight import sha256_file


class OnboardingRequest(BaseModel):
    mode: Literal["folders", "manifest"] = "folders"
    source_dir: str | None = None
    target_dir: str | None = None
    manifest_path: str | None = None
    id_regex: str | None = None
    seed: str = Field(default="foundation-v1", min_length=1)
    allow_visual_fallback: bool = True
    minimum_golden_cases: int = Field(default=3, ge=1, le=10000)


class OnboardingImportRequest(OnboardingRequest):
    snapshot_id: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    dataset_name: str = Field(min_length=1, max_length=120)
    confirmation: Literal["IMPORT"]


class OnboardingPreviewResponse(BaseModel):
    report: HistoricalOnboardingReport
    snapshot_id: str | None
    preview_token: str | None = None
    production_execution_enabled: bool = False


def build_preflight(request: OnboardingRequest) -> HistoricalOnboardingReport:
    builder = HistoricalOnboardingBuilder()
    if request.mode == "manifest":
        if not request.manifest_path or request.source_dir or request.target_dir:
            raise ValueError("Manifest mode requires manifest_path only")
        return builder.build_manifest(
            Path(request.manifest_path),
            seed=request.seed,
            minimum_golden_cases=request.minimum_golden_cases,
        )

    if not request.source_dir or not request.target_dir or request.manifest_path:
        raise ValueError("Folder mode requires source_dir and target_dir only")
    return builder.build_folders(
        Path(request.source_dir),
        Path(request.target_dir),
        id_regex=request.id_regex or None,
        seed=request.seed,
        allow_visual_fallback=request.allow_visual_fallback,
        minimum_golden_cases=request.minimum_golden_cases,
    )


def snapshot_id_for(report: HistoricalOnboardingReport) -> str | None:
    """Bind a ready preflight to the actual bytes, paths and import options.

    This read-only fingerprint expires automatically when files, selection,
    pairing or split seed change. No dataset is registered here.
    """
    if not report.ready_to_import:
        return None

    image_paths = {
        value
        for pair in report.pairs
        for value in [*pair.source_paths, pair.target_path, *pair.target_duplicate_paths]
    }
    file_hashes = [
        {"path": value, "sha256": sha256_file(Path(value))}
        for value in sorted(image_paths)
    ]
    manifest_hash = (
        sha256_file(Path(report.manifest_path))
        if report.manifest_path
        else None
    )
    payload = {
        "preflight": report.model_dump(
            mode="json",
            exclude={"created_at"},
        ),
        "files": file_hashes,
        "manifest_sha256": manifest_hash,
    }
    serialized = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def preview(request: OnboardingRequest) -> OnboardingPreviewResponse:
    report = build_preflight(request)
    return OnboardingPreviewResponse(
        report=report,
        snapshot_id=snapshot_id_for(report),
    )
