from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from pydantic import Field

from .contracts import DatasetSplit, ProviderAction, StrictModel
from .dataset_registry import DatasetRegistry
from .harness_models import BenchmarkRecipe, SRAdapterKind
from .providers import ProviderProtocolError, RemoteProvider
from .settings import Settings
from .sr_adapters import SRAdapterUnavailable, load_sr_adapter_spec


class GoldenPreflightStatus(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class GoldenLaneReadiness(StrictModel):
    ready: bool = False
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GoldenHoldoutPreflight(StrictModel):
    status: GoldenPreflightStatus
    ready_for_full_golden_chain: bool = False
    dataset_id: str | None = None
    dataset_name: str | None = None
    candidate_dataset_ids: list[str] = Field(default_factory=list)
    pair_count: int = 0
    artwork_count: int = 0
    golden_case_count: int = 0
    minimum_golden_cases: int = 3
    missing_golden_asset_count: int = 0
    golden_asset_fingerprint_mismatch_count: int = 0
    dataset_manifest_path: str | None = None
    dataset_manifest_sha256: str | None = None
    recipe_path: str | None = None
    recipe_sha256: str | None = None
    recipe_id: str | None = None
    recipe_version: str | None = None
    remote_provider_configured: bool = False
    remote_provider_recipe_valid: bool = False
    remote_provider_super_resolution_enabled: bool = False
    local_sr_adapter_path: str | None = None
    local_sr_adapter_ready: bool = False
    remote_sr_adapter_path: str | None = None
    remote_sr_adapter_ready: bool = False
    registration: GoldenLaneReadiness
    material_separation: GoldenLaneReadiness
    super_resolution: GoldenLaneReadiness
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    production_execution_enabled: bool = False
    created_at: datetime


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_optional(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path.expanduser().resolve()


def _local_executable_available(command: list[str]) -> bool:
    if not command:
        return False
    executable = command[0]
    path = Path(executable).expanduser()
    if path.is_absolute() or "/" in executable or "\\" in executable:
        return path.resolve().is_file()
    return shutil.which(executable) is not None


class GoldenHoldoutPreflightBuilder:
    """Read-only readiness check for running real Golden Holdout evidence."""

    def __init__(self, settings: Settings, registry: DatasetRegistry) -> None:
        self.settings = settings
        self.registry = registry

    def build(
        self,
        *,
        dataset_id: str | None = None,
        recipe_path: Path | None = None,
        local_sr_adapter_path: Path | None = None,
        remote_sr_adapter_path: Path | None = None,
        minimum_golden_cases: int = 3,
    ) -> GoldenHoldoutPreflight:
        minimum_golden_cases = max(1, minimum_golden_cases)
        blockers: list[str] = []
        warnings: list[str] = []
        candidate_dataset_ids = [item.dataset_id for item in self.registry.list_datasets()]

        dataset = None
        if dataset_id:
            dataset = self.registry.get_dataset(dataset_id)
            if dataset is None:
                blockers.append("golden_preflight_dataset_not_found")
        elif len(candidate_dataset_ids) == 1:
            dataset = self.registry.get_dataset(candidate_dataset_ids[0])
        elif not candidate_dataset_ids:
            blockers.append("golden_preflight_no_datasets_registered")
        else:
            blockers.append("golden_preflight_dataset_id_required")

        golden_case_count = 0
        missing_golden_asset_count = 0
        golden_asset_fingerprint_mismatch_count = 0
        manifest_path: Path | None = None
        manifest_sha256: str | None = None

        if dataset is not None:
            if dataset.pair_count <= 0 or dataset.artwork_count <= 0:
                blockers.append("golden_preflight_dataset_is_empty")

            manifest_path = Path(dataset.manifest_path).expanduser().resolve()
            if not manifest_path.is_file():
                blockers.append("golden_preflight_dataset_manifest_missing")
            else:
                manifest_sha256 = _sha256(manifest_path)
                try:
                    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                    embedded = payload.get("dataset") if isinstance(payload, dict) else None
                    embedded_id = embedded.get("dataset_id") if isinstance(embedded, dict) else None
                    if embedded_id != dataset.dataset_id:
                        blockers.append("golden_preflight_dataset_manifest_identity_mismatch")
                except (OSError, json.JSONDecodeError):
                    blockers.append("golden_preflight_dataset_manifest_invalid")

            golden_members = self.registry.list_members(
                dataset.dataset_id,
                split=DatasetSplit.GOLDEN_HOLDOUT,
            )
            golden_case_count = len(golden_members)
            if golden_case_count < minimum_golden_cases:
                blockers.append("golden_preflight_insufficient_golden_cases")

            for member in golden_members:
                pair = self.registry.get_pair(member.pair_id)
                if pair is None:
                    missing_golden_asset_count += 1
                    continue
                asset_ids = [pair.target_asset_id, *pair.source_asset_ids]
                for asset_id in asset_ids:
                    asset = self.registry.get_asset(asset_id)
                    if asset is None:
                        missing_golden_asset_count += 1
                        continue
                    locations = [
                        Path(value).expanduser()
                        for value in self.registry.list_asset_locations(asset_id)
                    ]
                    fallback = Path(asset.path).expanduser()
                    existing_path = next(
                        (path for path in locations if path.is_file()),
                        fallback if fallback.is_file() else None,
                    )
                    if existing_path is None:
                        missing_golden_asset_count += 1
                        continue
                    if _sha256(existing_path) != asset.sha256:
                        golden_asset_fingerprint_mismatch_count += 1
            if missing_golden_asset_count:
                blockers.append("golden_preflight_missing_golden_assets")
            if golden_asset_fingerprint_mismatch_count:
                blockers.append("golden_preflight_golden_asset_fingerprint_mismatch")

        resolved_recipe = _resolve_optional(recipe_path)
        if resolved_recipe is None:
            recipe_candidates = []
            if getattr(sys, "frozen", False):
                # Windows release: the engine and packaged config are siblings.
                recipe_candidates.append(
                    Path(sys.executable).resolve().parent
                    / "config"
                    / "benchmark-recipe.local.json"
                )
            recipe_candidates.extend([
                Path.cwd() / "config" / "benchmark-recipe.local.json",
                Path(__file__).resolve().parents[2]
                / "config"
                / "benchmark-recipe.local.json",
            ])
            resolved_recipe = next(
                (candidate.resolve() for candidate in recipe_candidates if candidate.is_file()),
                None,
            )

        recipe_sha256: str | None = None
        recipe_id: str | None = None
        recipe_version: str | None = None
        if resolved_recipe is None or not resolved_recipe.is_file():
            blockers.append("golden_preflight_benchmark_recipe_missing")
        else:
            try:
                recipe = BenchmarkRecipe.model_validate_json(
                    resolved_recipe.read_text(encoding="utf-8")
                )
                recipe_sha256 = _sha256(resolved_recipe)
                recipe_id = recipe.recipe_id
                recipe_version = recipe.version
            except Exception:
                blockers.append("golden_preflight_benchmark_recipe_invalid")

        remote_provider = RemoteProvider(self.settings)
        remote_provider_configured = remote_provider.available
        remote_provider_recipe_valid = False
        remote_provider_sr_enabled = False
        if remote_provider_configured:
            try:
                provider_recipe = remote_provider.recipe()
                remote_provider_recipe_valid = True
                sr_action = provider_recipe.for_action(ProviderAction.SUPER_RESOLUTION)
                remote_provider_sr_enabled = bool(sr_action is not None and sr_action.enabled)
            except ProviderProtocolError:
                warnings.append("golden_preflight_remote_provider_recipe_invalid")
        else:
            warnings.append("golden_preflight_remote_provider_not_configured")

        local_adapter_path = _resolve_optional(local_sr_adapter_path)
        local_sr_ready = False
        if local_adapter_path is not None:
            try:
                spec = load_sr_adapter_spec(local_adapter_path)
                local_sr_ready = (
                    spec.kind is SRAdapterKind.LOCAL_COMMAND
                    and _local_executable_available(spec.command)
                )
                if not local_sr_ready:
                    warnings.append("golden_preflight_local_sr_adapter_unavailable")
            except (FileNotFoundError, SRAdapterUnavailable):
                warnings.append("golden_preflight_local_sr_adapter_invalid")

        remote_adapter_path = _resolve_optional(remote_sr_adapter_path)
        remote_sr_ready = False
        if remote_adapter_path is not None:
            try:
                spec = load_sr_adapter_spec(remote_adapter_path)
                remote_sr_ready = (
                    spec.kind is SRAdapterKind.REMOTE_PROVIDER
                    and remote_provider_configured
                    and remote_provider_recipe_valid
                    and remote_provider_sr_enabled
                )
                if not remote_sr_ready:
                    warnings.append("golden_preflight_remote_sr_adapter_unavailable")
            except (FileNotFoundError, SRAdapterUnavailable):
                warnings.append("golden_preflight_remote_sr_adapter_invalid")

        common_blockers = list(dict.fromkeys(blockers))
        registration = GoldenLaneReadiness(
            ready=not common_blockers,
            blockers=common_blockers,
            warnings=list(dict.fromkeys(warnings)),
        )
        material = GoldenLaneReadiness(
            ready=not common_blockers,
            blockers=common_blockers,
            warnings=list(dict.fromkeys(warnings)),
        )

        sr_blockers = list(common_blockers)
        if not (local_sr_ready or remote_sr_ready):
            sr_blockers.append("golden_preflight_sr_backend_unavailable")
        super_resolution = GoldenLaneReadiness(
            ready=not sr_blockers,
            blockers=list(dict.fromkeys(sr_blockers)),
            warnings=list(dict.fromkeys(warnings)),
        )

        ready_count = sum(
            [
                int(registration.ready),
                int(material.ready),
                int(super_resolution.ready),
            ]
        )
        if ready_count == 3:
            status = GoldenPreflightStatus.READY
        elif ready_count:
            status = GoldenPreflightStatus.PARTIAL
        else:
            status = GoldenPreflightStatus.BLOCKED

        return GoldenHoldoutPreflight(
            status=status,
            ready_for_full_golden_chain=ready_count == 3,
            dataset_id=dataset.dataset_id if dataset is not None else dataset_id,
            dataset_name=dataset.name if dataset is not None else None,
            candidate_dataset_ids=candidate_dataset_ids,
            pair_count=dataset.pair_count if dataset is not None else 0,
            artwork_count=dataset.artwork_count if dataset is not None else 0,
            golden_case_count=golden_case_count,
            minimum_golden_cases=minimum_golden_cases,
            missing_golden_asset_count=missing_golden_asset_count,
            golden_asset_fingerprint_mismatch_count=(
                golden_asset_fingerprint_mismatch_count
            ),
            dataset_manifest_path=str(manifest_path) if manifest_path is not None else None,
            dataset_manifest_sha256=manifest_sha256,
            recipe_path=str(resolved_recipe) if resolved_recipe is not None else None,
            recipe_sha256=recipe_sha256,
            recipe_id=recipe_id,
            recipe_version=recipe_version,
            remote_provider_configured=remote_provider_configured,
            remote_provider_recipe_valid=remote_provider_recipe_valid,
            remote_provider_super_resolution_enabled=remote_provider_sr_enabled,
            local_sr_adapter_path=(
                str(local_adapter_path) if local_adapter_path is not None else None
            ),
            local_sr_adapter_ready=local_sr_ready,
            remote_sr_adapter_path=(
                str(remote_adapter_path) if remote_adapter_path is not None else None
            ),
            remote_sr_adapter_ready=remote_sr_ready,
            registration=registration,
            material_separation=material,
            super_resolution=super_resolution,
            blockers=common_blockers,
            warnings=list(dict.fromkeys(warnings)),
            production_execution_enabled=False,
            created_at=datetime.now(timezone.utc),
        )
