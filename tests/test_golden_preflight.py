from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

from pod_artwork_engine.contracts import DatasetSplit
from pod_artwork_engine.dataset_registry import DatasetRegistry
from pod_artwork_engine.golden_preflight import (
    GoldenHoldoutPreflightBuilder,
    GoldenPreflightStatus,
)
from pod_artwork_engine.harness_models import (
    BenchmarkRecipe,
    SRAdapterKind,
    SRAdapterSpec,
)
from pod_artwork_engine.settings import Settings


def _image(path: Path, color: tuple[int, int, int]) -> Path:
    Image.new("RGB", (96, 96), color).save(path)
    return path


def _recipe(path: Path) -> Path:
    recipe = BenchmarkRecipe(
        recipe_id="local-precision-test",
        version="22",
    )
    path.write_text(recipe.model_dump_json(indent=2), encoding="utf-8")
    return path


def _seed_dataset(tmp_path: Path) -> tuple[Settings, DatasetRegistry, str]:
    settings = Settings(data_root=tmp_path / "runtime")
    registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
    for index in range(3):
        source = _image(
            tmp_path / f"source-{index}.png",
            (20 + index * 30, 40 + index * 20, 70 + index * 10),
        )
        target = _image(
            tmp_path / f"target-{index}.png",
            (180 - index * 20, 80 + index * 30, 40 + index * 40),
        )
        registry.register_pair(f"design-{index}", [source], target)
    dataset = registry.create_dataset("golden-preflight-test")
    return settings, registry, dataset.dataset_id


def test_preflight_blocks_when_no_dataset_is_registered(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "runtime")
    registry = DatasetRegistry(settings.database_path, settings.datasets_dir)

    report = GoldenHoldoutPreflightBuilder(settings, registry).build(
        recipe_path=_recipe(tmp_path / "recipe.json"),
        minimum_golden_cases=1,
    )

    assert report.status is GoldenPreflightStatus.BLOCKED
    assert report.registration.ready is False
    assert report.material_separation.ready is False
    assert report.super_resolution.ready is False
    assert "golden_preflight_no_datasets_registered" in report.blockers
    assert report.production_execution_enabled is False


def test_dataset_and_recipe_make_registration_and_material_ready(
    tmp_path: Path,
) -> None:
    settings, registry, dataset_id = _seed_dataset(tmp_path)

    report = GoldenHoldoutPreflightBuilder(settings, registry).build(
        dataset_id=dataset_id,
        recipe_path=_recipe(tmp_path / "recipe.json"),
        minimum_golden_cases=1,
    )

    assert report.status is GoldenPreflightStatus.PARTIAL
    assert report.registration.ready is True
    assert report.material_separation.ready is True
    assert report.super_resolution.ready is False
    assert report.golden_case_count >= 1
    assert report.dataset_manifest_sha256 is not None
    assert len(report.dataset_manifest_sha256) == 64
    assert report.recipe_id == "local-precision-test"
    assert "golden_preflight_sr_backend_unavailable" in report.super_resolution.blockers
    assert report.production_execution_enabled is False


def test_available_local_sr_adapter_makes_full_chain_ready(tmp_path: Path) -> None:
    settings, registry, dataset_id = _seed_dataset(tmp_path)
    adapter_path = tmp_path / "local-sr.json"
    adapter = SRAdapterSpec(
        adapter_id="local-python-test",
        kind=SRAdapterKind.LOCAL_COMMAND,
        command=[sys.executable, "{input}", "{output}"],
    )
    adapter_path.write_text(adapter.model_dump_json(indent=2), encoding="utf-8")

    report = GoldenHoldoutPreflightBuilder(settings, registry).build(
        dataset_id=dataset_id,
        recipe_path=_recipe(tmp_path / "recipe.json"),
        local_sr_adapter_path=adapter_path,
        minimum_golden_cases=1,
    )

    assert report.status is GoldenPreflightStatus.READY
    assert report.ready_for_full_golden_chain is True
    assert report.local_sr_adapter_ready is True
    assert report.registration.ready is True
    assert report.material_separation.ready is True
    assert report.super_resolution.ready is True
    assert report.production_execution_enabled is False


def test_missing_golden_asset_blocks_all_lanes(tmp_path: Path) -> None:
    settings, registry, dataset_id = _seed_dataset(tmp_path)
    golden_members = registry.list_members(
        dataset_id,
        split=DatasetSplit.GOLDEN_HOLDOUT,
    )
    assert golden_members
    pair = registry.get_pair(golden_members[0].pair_id)
    assert pair is not None
    target = registry.get_asset(pair.target_asset_id)
    assert target is not None
    Path(target.path).unlink()

    report = GoldenHoldoutPreflightBuilder(settings, registry).build(
        dataset_id=dataset_id,
        recipe_path=_recipe(tmp_path / "recipe.json"),
        minimum_golden_cases=1,
    )

    assert report.status is GoldenPreflightStatus.BLOCKED
    assert report.missing_golden_asset_count >= 1
    assert "golden_preflight_missing_golden_assets" in report.blockers
    assert report.registration.ready is False
    assert report.material_separation.ready is False
    assert report.production_execution_enabled is False


def test_golden_asset_content_drift_is_detected(tmp_path: Path) -> None:
    settings, registry, dataset_id = _seed_dataset(tmp_path)
    golden_members = registry.list_members(
        dataset_id,
        split=DatasetSplit.GOLDEN_HOLDOUT,
    )
    assert golden_members
    pair = registry.get_pair(golden_members[0].pair_id)
    assert pair is not None
    target = registry.get_asset(pair.target_asset_id)
    assert target is not None
    _image(Path(target.path), (1, 2, 3))

    report = GoldenHoldoutPreflightBuilder(settings, registry).build(
        dataset_id=dataset_id,
        recipe_path=_recipe(tmp_path / "recipe.json"),
        minimum_golden_cases=1,
    )

    assert report.status is GoldenPreflightStatus.BLOCKED
    assert report.golden_asset_fingerprint_mismatch_count >= 1
    assert (
        "golden_preflight_golden_asset_fingerprint_mismatch"
        in report.blockers
    )
    assert report.production_execution_enabled is False


def test_manifest_identity_drift_is_detected(tmp_path: Path) -> None:
    settings, registry, dataset_id = _seed_dataset(tmp_path)
    dataset = registry.get_dataset(dataset_id)
    assert dataset is not None
    manifest_path = Path(dataset.manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["dataset"]["dataset_id"] = "tampered-dataset"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    report = GoldenHoldoutPreflightBuilder(settings, registry).build(
        dataset_id=dataset_id,
        recipe_path=_recipe(tmp_path / "recipe.json"),
        minimum_golden_cases=1,
    )

    assert report.status is GoldenPreflightStatus.BLOCKED
    assert "golden_preflight_dataset_manifest_identity_mismatch" in report.blockers
    assert report.production_execution_enabled is False
