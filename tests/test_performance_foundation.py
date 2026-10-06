from pathlib import Path

import pytest
from PIL import Image

from pod_artwork_engine.contracts import ProviderAction, ProviderRequest, QualityMode
from pod_artwork_engine.performance import PerformanceStore, StageCache
from pod_artwork_engine.providers import ProviderUnavailable, RemoteProvider
from pod_artwork_engine.scheduler import recommended_job_concurrency
from pod_artwork_engine.settings import GIB, Settings


def test_stage_cache_is_content_addressed_and_versioned(tmp_path: Path) -> None:
    cache = StageCache(tmp_path / "cache")
    source_hash = "a" * 64
    payload = {"value": 42}

    cache.put("preflight", "v1", [source_hash], payload)

    assert cache.get("preflight", "v1", [source_hash]) == payload
    assert cache.get("preflight", "v2", [source_hash]) is None
    assert cache.get("preflight", "v1", ["b" * 64]) is None


def test_performance_store_reports_percentiles_and_cache_rate(tmp_path: Path) -> None:
    store = PerformanceStore(tmp_path / "engine.sqlite3")
    for index, duration in enumerate((10, 20, 30, 100)):
        store.record(
            job_id=f"job-{index}",
            stage="preflight",
            event="preflight_completed",
            duration_ms=duration,
            cache_hit=index < 2,
        )

    summary = store.summary()["preflight"]

    assert summary["count"] == 4
    assert summary["p50_ms"] == 30
    assert summary["p95_ms"] == 100
    assert summary["max_ms"] == 100
    assert summary["cache_hits"] == 2
    assert summary["cache_hit_rate"] == 0.5


def test_scheduler_honors_explicit_concurrency_override(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path,
        cpu_soft_threads=40,
        ram_soft_bytes=32 * GIB,
        max_concurrent_jobs=3,
    )

    assert recommended_job_concurrency(settings) == 3


def test_scheduler_auto_mode_is_conservative(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path,
        cpu_soft_threads=128,
        ram_soft_bytes=128 * GIB,
        max_concurrent_jobs=0,
    )

    assert 1 <= recommended_job_concurrency(settings) <= 4


def test_remote_provider_circuit_breaker_opens_after_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (32, 32), (20, 30, 40)).save(source)
    settings = Settings(
        data_root=tmp_path,
        remote_provider_url="http://provider.invalid/gateway",
        remote_provider_failure_threshold=2,
        remote_provider_cooldown_seconds=60,
    )
    provider = RemoteProvider(settings)
    request = ProviderRequest(
        action=ProviderAction.ANALYZE,
        job_id="job-circuit",
        quality_mode=QualityMode.PRINT_READY,
        source_paths=[str(source)],
    )

    def fail_request(*_args, **_kwargs):
        raise OSError("provider down")

    monkeypatch.setattr("urllib.request.urlopen", fail_request)

    with pytest.raises(ProviderUnavailable):
        provider.execute(request)
    assert provider.available is True

    with pytest.raises(ProviderUnavailable):
        provider.execute(request)

    state = provider.status()
    assert provider.available is False
    assert state["failure_count"] == 2
    assert float(state["cooldown_remaining_seconds"]) > 0

    with pytest.raises(ProviderUnavailable, match="circuit open"):
        provider.execute(request)
