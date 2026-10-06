from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

from pod_artwork_engine.contracts import DatasetSplit
from pod_artwork_engine.dataset_registry import DatasetRegistry
from pod_artwork_engine.harness import HarnessStore, load_candidate_manifest
from pod_artwork_engine.harness_models import (
    BenchmarkTier,
    CandidateManifest,
    CandidateManifestEntry,
    SRAdapterKind,
    SRAdapterSpec,
    SRCohortSpec,
)
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.sr_adapters import SRAdapterMaterializer
from pod_artwork_engine.sr_cohort import SRCohortMaterializer


def _image(
    path: Path,
    *,
    size: tuple[int, int] = (48, 36),
    color: tuple[int, int, int] = (40, 120, 210),
) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


def _fixture(tmp_path: Path):
    settings = Settings(data_root=tmp_path / "runtime")
    registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
    source = _image(tmp_path / "historical-source.png", color=(15, 45, 75))
    target = _image(
        tmp_path / "ground-truth.png",
        size=(120, 90),
        color=(210, 40, 70),
    )
    pair = registry.register_pair("cohort-design", [source], target)
    dataset = registry.create_dataset("cohort-fixture", seed="phase2j")
    member = next(
        item
        for item in registry.list_members(dataset.dataset_id)
        if item.pair_id == pair.pair_id
    )
    tier = {
        DatasetSplit.TRAIN: BenchmarkTier.SMOKE,
        DatasetSplit.VALIDATION: BenchmarkTier.REGRESSION,
        DatasetSplit.GOLDEN_HOLDOUT: BenchmarkTier.GOLDEN,
    }[member.split]
    pre_sr = _image(
        tmp_path / "pre-sr.png",
        size=(48, 36),
        color=(60, 140, 220),
    )
    input_manifest = CandidateManifest(
        candidates={
            pair.pair_id: CandidateManifestEntry(
                result_path=str(pre_sr),
                metadata={"stage": "pre_sr"},
            )
        }
    )
    manifest_path = tmp_path / "pre-sr-candidates.json"
    manifest_path.write_text(input_manifest.model_dump_json(indent=2), encoding="utf-8")
    return settings, registry, dataset.dataset_id, pair, tier, manifest_path


def test_cohort_materializes_shared_native_and_lanczos_identity(
    tmp_path: Path,
) -> None:
    settings, registry, dataset_id, pair, tier, manifest_path = _fixture(tmp_path)
    store = HarnessStore(settings.harness_dir)
    spec = SRCohortSpec(
        cohort_id="cohort-shared",
        dataset_id=dataset_id,
        tier=tier,
        input_manifest_path=str(manifest_path),
        scale_factor=2.0,
        limit=1,
    )

    report = SRCohortMaterializer(settings, registry, store).materialize(spec)

    assert report.success_count == 1
    assert report.failure_count == 0
    assert report.shared_input_identity is True
    source = load_candidate_manifest(Path(report.source_manifest_path))
    native = load_candidate_manifest(Path(report.native_manifest_path))
    lanczos = load_candidate_manifest(Path(report.lanczos_manifest_path))
    source_entry = source.candidates[pair.pair_id]
    native_entry = native.candidates[pair.pair_id]
    lanczos_entry = lanczos.candidates[pair.pair_id]

    assert source_entry.result_path == native_entry.result_path
    with Image.open(native_entry.result_path) as image:
        assert image.size == (48, 36)
    with Image.open(lanczos_entry.result_path) as image:
        assert image.size == (96, 72)

    source_meta = source_entry.metadata["sr_cohort"]
    native_meta = native_entry.metadata["sr_cohort"]
    lanczos_meta = lanczos_entry.metadata["sr_cohort"]
    assert source_meta["source_input_sha256"] == native_meta["source_input_sha256"]
    assert source_meta["source_input_sha256"] == lanczos_meta["source_input_sha256"]
    assert source_meta["cohort_id"] == "cohort-shared"
    assert native_meta["lane"] == "native"
    assert lanczos_meta["lane"] == "lanczos"
    assert lanczos_meta["transform"] == "lanczos_resize"


def test_lanczos_materialization_is_deterministic(tmp_path: Path) -> None:
    settings, registry, dataset_id, pair, tier, manifest_path = _fixture(tmp_path)
    store = HarnessStore(settings.harness_dir)
    materializer = SRCohortMaterializer(settings, registry, store)
    first = materializer.materialize(
        SRCohortSpec(
            cohort_id="cohort-a",
            dataset_id=dataset_id,
            tier=tier,
            input_manifest_path=str(manifest_path),
            scale_factor=2.0,
            limit=1,
        )
    )
    second = materializer.materialize(
        SRCohortSpec(
            cohort_id="cohort-b",
            dataset_id=dataset_id,
            tier=tier,
            input_manifest_path=str(manifest_path),
            scale_factor=2.0,
            limit=1,
        )
    )

    first_path = Path(
        load_candidate_manifest(Path(first.lanczos_manifest_path))
        .candidates[pair.pair_id]
        .result_path
    )
    second_path = Path(
        load_candidate_manifest(Path(second.lanczos_manifest_path))
        .candidates[pair.pair_id]
        .result_path
    )
    assert first_path.read_bytes() == second_path.read_bytes()


def test_ground_truth_equivalent_input_is_rejected(tmp_path: Path) -> None:
    settings, registry, dataset_id, pair, tier, _ = _fixture(tmp_path)
    case = SRCohortMaterializer(
        settings,
        registry,
        HarnessStore(settings.harness_dir),
    ).case_factory.build(dataset_id, tier, limit=1)[0]
    leak_manifest = CandidateManifest(
        candidates={
            pair.pair_id: CandidateManifestEntry(result_path=case.target_path)
        }
    )
    leak_path = tmp_path / "leak.json"
    leak_path.write_text(leak_manifest.model_dump_json(indent=2), encoding="utf-8")

    report = SRCohortMaterializer(
        settings,
        registry,
        HarnessStore(settings.harness_dir),
    ).materialize(
        SRCohortSpec(
            dataset_id=dataset_id,
            tier=tier,
            input_manifest_path=str(leak_path),
            limit=1,
        )
    )

    assert report.success_count == 0
    assert report.failure_count == 1
    assert report.cases[0].fail_closed is True
    assert "ground-truth" in report.cases[0].reasons[0]


def test_missing_input_or_capacity_pressure_fails_closed(tmp_path: Path) -> None:
    settings, registry, dataset_id, pair, tier, manifest_path = _fixture(tmp_path)
    missing = CandidateManifest(
        candidates={
            pair.pair_id: CandidateManifestEntry(
                result_path=str(tmp_path / "missing.png")
            )
        }
    )
    missing_path = tmp_path / "missing-manifest.json"
    missing_path.write_text(missing.model_dump_json(indent=2), encoding="utf-8")
    materializer = SRCohortMaterializer(
        settings,
        registry,
        HarnessStore(settings.harness_dir),
    )
    missing_report = materializer.materialize(
        SRCohortSpec(
            cohort_id="missing-input",
            dataset_id=dataset_id,
            tier=tier,
            input_manifest_path=str(missing_path),
            limit=1,
        )
    )
    assert missing_report.failure_count == 1

    pressured = SRCohortMaterializer(
        settings,
        registry,
        HarnessStore(settings.harness_dir),
    )
    pressured._capacity_ok = lambda **_: False
    pressure_report = pressured.materialize(
        SRCohortSpec(
            cohort_id="capacity-pressure",
            dataset_id=dataset_id,
            tier=tier,
            input_manifest_path=str(manifest_path),
            limit=1,
        )
    )
    assert pressure_report.failure_count == 1
    assert "storage hard cap" in pressure_report.cases[0].reasons[0]


def _resize_script(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "import sys",
                "from PIL import Image",
                "with Image.open(sys.argv[1]) as source:",
                "    scale = float(sys.argv[3])",
                "    source.resize((round(source.width*scale), round(source.height*scale)), Image.Resampling.NEAREST).save(sys.argv[2])",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_sr_adapter_preserves_cohort_identity(tmp_path: Path) -> None:
    settings, registry, dataset_id, pair, tier, manifest_path = _fixture(tmp_path)
    store = HarnessStore(settings.harness_dir)
    cohort = SRCohortMaterializer(settings, registry, store).materialize(
        SRCohortSpec(
            cohort_id="adapter-cohort",
            dataset_id=dataset_id,
            tier=tier,
            input_manifest_path=str(manifest_path),
            scale_factor=2.0,
            limit=1,
        )
    )
    script = _resize_script(tmp_path / "resize.py")
    adapter = SRAdapterMaterializer(settings, registry, store).materialize(
        dataset_id=dataset_id,
        tier=tier,
        source_manifest_path=Path(cohort.source_manifest_path),
        adapter_spec=SRAdapterSpec(
            adapter_id="fixture",
            kind=SRAdapterKind.LOCAL_COMMAND,
            scale_factor=2.0,
            min_output_scale=1.5,
            command=[
                sys.executable,
                str(script),
                "{input}",
                "{output}",
                "{scale}",
            ],
        ),
        limit=1,
    )
    output = load_candidate_manifest(Path(adapter.output_manifest_path))
    metadata = output.candidates[pair.pair_id].metadata["sr_cohort"]
    assert metadata["cohort_id"] == "adapter-cohort"
    source_manifest = load_candidate_manifest(Path(cohort.source_manifest_path))
    assert (
        metadata["source_input_sha256"]
        == source_manifest.candidates[pair.pair_id]
        .metadata["sr_cohort"]["source_input_sha256"]
    )
