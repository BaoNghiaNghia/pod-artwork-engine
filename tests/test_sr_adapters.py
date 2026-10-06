from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

from pod_artwork_engine.contracts import (
    DatasetSplit,
    ProviderAction,
    ProviderResult,
    QualityMode,
)
from pod_artwork_engine.dataset_registry import DatasetRegistry
from pod_artwork_engine.harness import HarnessStore, load_candidate_manifest
from pod_artwork_engine.harness_models import (
    BenchmarkTier,
    CandidateManifest,
    CandidateManifestEntry,
    SRAdapterKind,
    SRAdapterSpec,
)
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.sr_adapters import SRAdapterMaterializer


def _image(
    path: Path,
    *,
    size: tuple[int, int] = (48, 36),
    color: tuple[int, int, int] = (40, 120, 210),
) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


def _dataset(tmp_path: Path):
    settings = Settings(data_root=tmp_path / "runtime")
    registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
    source = _image(tmp_path / "source.png")
    target = _image(
        tmp_path / "target.png",
        size=(96, 72),
        color=(30, 100, 200),
    )
    pair = registry.register_pair("sr-adapter-case", [source], target)
    dataset = registry.create_dataset("sr-adapter-dataset", seed="sr-adapter-test")
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
    return settings, registry, dataset.dataset_id, pair, tier


def _source_manifest(
    tmp_path: Path,
    pair_id: str,
    *,
    size: tuple[int, int] = (48, 36),
) -> Path:
    candidate = _image(
        tmp_path / "native-candidate.png",
        size=size,
        color=(60, 130, 220),
    )
    manifest = CandidateManifest(
        candidates={
            pair_id: CandidateManifestEntry(
                result_path=str(candidate),
                metadata={"baseline": "native"},
            )
        }
    )
    path = tmp_path / "native-candidates.json"
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return path


def _resize_script(path: Path, *, honor_scale: bool = True) -> Path:
    scale_line = (
        "scale = float(sys.argv[3])"
        if honor_scale
        else "scale = 1.0"
    )
    path.write_text(
        "\n".join(
            [
                "import sys",
                "from PIL import Image",
                scale_line,
                "with Image.open(sys.argv[1]) as source:",
                "    image = source.convert('RGB')",
                "    width = max(1, round(image.width * scale))",
                "    height = max(1, round(image.height * scale))",
                "    image.resize((width, height), Image.Resampling.NEAREST).save(sys.argv[2])",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_local_adapter_spec_requires_input_and_output_placeholders() -> None:
    with pytest.raises(ValueError, match=r"requires \{input\} and \{output\}"):
        SRAdapterSpec(
            adapter_id="broken",
            kind=SRAdapterKind.LOCAL_COMMAND,
            command=["sr.exe", "--scale", "{scale}"],
        )


def test_local_command_materializes_real_candidate_manifest(
    tmp_path: Path,
) -> None:
    settings, registry, dataset_id, pair, tier = _dataset(tmp_path)
    source_manifest = _source_manifest(tmp_path, pair.pair_id)
    script = _resize_script(tmp_path / "resize_sr.py")
    spec = SRAdapterSpec(
        adapter_id="fixture-local-sr",
        version="1",
        kind=SRAdapterKind.LOCAL_COMMAND,
        model_alias="fixture-nearest",
        scale_factor=2.0,
        min_output_scale=1.5,
        command=[
            sys.executable,
            str(script),
            "{input}",
            "{output}",
            "{scale}",
        ],
    )

    store = HarnessStore(settings.harness_dir)
    report = SRAdapterMaterializer(
        settings,
        registry,
        store,
    ).materialize(
        dataset_id=dataset_id,
        tier=tier,
        source_manifest_path=source_manifest,
        adapter_spec=spec,
        limit=1,
    )

    assert report.backend_available is True
    assert report.success_count == 1
    assert report.failure_count == 0
    assert report.production_execution_enabled is False

    manifest = load_candidate_manifest(Path(report.output_manifest_path))
    entry = manifest.candidates[pair.pair_id]
    output = Path(entry.result_path)
    assert output.is_file()
    with Image.open(output) as image:
        assert image.size == (96, 72)
    assert entry.operational.latency_ms > 0
    assert entry.operational.provider_calls == 0
    assert entry.metadata["benchmark_only"] is True
    adapter = entry.metadata["sr_adapter"]
    assert adapter["kind"] == "local_command"
    assert adapter["measured_scale"] == pytest.approx(2.0)
    assert adapter["input_width"] == 48
    assert adapter["output_width"] == 96
    assert (
        store.sr_adapter_dir(report.run_id) / "adapter-spec.json"
    ).is_file()


def test_missing_local_backend_fails_closed_without_fake_output(
    tmp_path: Path,
) -> None:
    settings, registry, dataset_id, pair, tier = _dataset(tmp_path)
    source_manifest = _source_manifest(tmp_path, pair.pair_id)
    spec = SRAdapterSpec(
        adapter_id="missing-local-sr",
        kind=SRAdapterKind.LOCAL_COMMAND,
        command=[
            str(tmp_path / "missing-sr.exe"),
            "{input}",
            "{output}",
        ],
    )

    report = SRAdapterMaterializer(
        settings,
        registry,
        HarnessStore(settings.harness_dir),
    ).materialize(
        dataset_id=dataset_id,
        tier=tier,
        source_manifest_path=source_manifest,
        adapter_spec=spec,
        limit=1,
    )

    assert report.backend_available is False
    assert report.success_count == 0
    assert report.failure_count == 1
    manifest = load_candidate_manifest(Path(report.output_manifest_path))
    entry = manifest.candidates[pair.pair_id]
    assert not Path(entry.result_path).exists()
    assert entry.operational.manual_review is True
    assert entry.precision.super_resolution_fail_closed is True
    assert entry.metadata["fail_closed"] is True
    assert "executable is not available" in entry.metadata["failure_reason"]


def test_local_output_below_minimum_scale_is_rejected(
    tmp_path: Path,
) -> None:
    settings, registry, dataset_id, pair, tier = _dataset(tmp_path)
    source_manifest = _source_manifest(tmp_path, pair.pair_id)
    script = _resize_script(tmp_path / "same_size.py", honor_scale=False)
    spec = SRAdapterSpec(
        adapter_id="bad-scale",
        kind=SRAdapterKind.LOCAL_COMMAND,
        scale_factor=2.0,
        min_output_scale=1.2,
        command=[
            sys.executable,
            str(script),
            "{input}",
            "{output}",
            "{scale}",
        ],
    )

    report = SRAdapterMaterializer(
        settings,
        registry,
        HarnessStore(settings.harness_dir),
    ).materialize(
        dataset_id=dataset_id,
        tier=tier,
        source_manifest_path=source_manifest,
        adapter_spec=spec,
        limit=1,
    )

    assert report.failure_count == 1
    manifest = load_candidate_manifest(Path(report.output_manifest_path))
    entry = manifest.candidates[pair.pair_id]
    assert not Path(entry.result_path).exists()
    assert "below minimum" in entry.metadata["failure_reason"]


def _encoded_png(size: tuple[int, int]) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", size, (90, 140, 220)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def test_remote_adapter_uses_explicit_provider_sr_action_and_metrics(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings, registry, dataset_id, pair, tier = _dataset(tmp_path)
    source_manifest = _source_manifest(tmp_path, pair.pair_id)
    provider_recipe = tmp_path / "provider-recipe.json"
    provider_recipe.write_text(
        json.dumps(
            {
                "recipe_id": "sr-provider-test",
                "version": "1",
                "provider_name": "fixture-provider",
                "actions": [
                    {
                        "action": "super_resolution",
                        "enabled": True,
                        "model_alias": "fixture-sr",
                        "timeout_seconds": 30,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    remote_settings = Settings(
        data_root=settings.data_root,
        remote_provider_url="https://provider.invalid/sr",
        remote_provider_name="fixture-provider",
        provider_recipe_path=provider_recipe,
    )
    materializer = SRAdapterMaterializer(
        remote_settings,
        registry,
        HarnessStore(remote_settings.harness_dir),
    )
    calls: list[ProviderAction] = []

    def fake_execute(request):
        calls.append(request.action)
        assert request.action is ProviderAction.SUPER_RESOLUTION
        assert request.requested_capabilities == [
            "super_resolution",
            "scale:2",
        ]
        return ProviderResult(
            provider="fixture-provider",
            model_version="fixture-sr-v2",
            candidate_image_base64=_encoded_png((96, 72)),
            recognized_text=["FRESH OCR"],
            metadata={
                "cost_usd": 0.025,
                "peak_ram_mb": 640,
                "peak_vram_mb": 512,
                "reason_codes": ["remote_sr_fixture"],
                "hallucination_risk": False,
            },
        )

    monkeypatch.setattr(materializer.remote_provider, "execute", fake_execute)
    spec = SRAdapterSpec(
        adapter_id="remote-fixture",
        kind=SRAdapterKind.REMOTE_PROVIDER,
        model_alias="fixture-sr",
        scale_factor=2.0,
        min_output_scale=1.5,
    )

    report = materializer.materialize(
        dataset_id=dataset_id,
        tier=tier,
        source_manifest_path=source_manifest,
        adapter_spec=spec,
        quality_mode=QualityMode.PRINT_READY,
        limit=1,
    )

    assert calls == [ProviderAction.SUPER_RESOLUTION]
    assert report.success_count == 1
    manifest = load_candidate_manifest(Path(report.output_manifest_path))
    entry = manifest.candidates[pair.pair_id]
    assert entry.recognized_text == ["FRESH OCR"]
    assert entry.operational.provider_calls == 1
    assert entry.operational.cost_usd == pytest.approx(0.025)
    assert entry.operational.peak_ram_mb == pytest.approx(640)
    assert entry.operational.peak_vram_mb == pytest.approx(512)
    assert entry.metadata["reason_codes"] == ["remote_sr_fixture"]
    assert entry.metadata["sr_adapter"]["model_alias"] == "fixture-sr-v2"


def test_remote_adapter_without_explicit_provider_action_fails_closed(
    tmp_path: Path,
) -> None:
    settings, registry, dataset_id, pair, tier = _dataset(tmp_path)
    source_manifest = _source_manifest(tmp_path, pair.pair_id)
    provider_recipe = tmp_path / "provider-recipe.json"
    provider_recipe.write_text(
        json.dumps(
            {
                "recipe_id": "no-sr",
                "provider_name": "fixture-provider",
                "actions": [
                    {
                        "action": "analyze",
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    remote_settings = Settings(
        data_root=settings.data_root,
        remote_provider_url="https://provider.invalid/sr",
        provider_recipe_path=provider_recipe,
    )
    spec = SRAdapterSpec(
        adapter_id="remote-unavailable",
        kind=SRAdapterKind.REMOTE_PROVIDER,
    )

    report = SRAdapterMaterializer(
        remote_settings,
        registry,
        HarnessStore(remote_settings.harness_dir),
    ).materialize(
        dataset_id=dataset_id,
        tier=tier,
        source_manifest_path=source_manifest,
        adapter_spec=spec,
        limit=1,
    )

    assert report.backend_available is False
    assert report.failure_count == 1
    assert any(
        "not explicitly configured" in reason
        for reason in report.reasons
    )


def test_remote_adapter_rejects_local_command_tokens() -> None:
    with pytest.raises(ValueError, match="must not define a local command"):
        SRAdapterSpec(
            adapter_id="bad-remote",
            kind=SRAdapterKind.REMOTE_PROVIDER,
            command=["sr.exe", "{input}", "{output}"],
        )
