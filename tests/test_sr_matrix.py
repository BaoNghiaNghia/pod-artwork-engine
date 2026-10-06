from __future__ import annotations

from pathlib import Path

import pytest

from pod_artwork_engine.harness import HarnessStore
from pod_artwork_engine.harness_models import (
    BenchmarkCaseResult,
    BenchmarkRecipe,
    BenchmarkScorecard,
    BenchmarkTier,
    HarnessRunStatus,
    OperationalMetrics,
    PrecisionEvidence,
    QualityMode,
    RunProvenance,
    SRBenchmarkLane,
    SRBenchmarkRecommendation,
    SRBenchmarkSpec,
    SRCasePreference,
    SemanticMetrics,
    TechnicalMetrics,
)
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.sr_matrix import SRBenchmarkMatrixRunner


def _recipe(path: Path) -> Path:
    recipe = BenchmarkRecipe(
        recipe_id="sr-test-v1",
        version="1",
        description="SR benchmark test recipe",
    )
    path.write_text(recipe.model_dump_json(indent=2), encoding="utf-8")
    return path


def _result(
    *,
    quality: float,
    detail: float,
    semantic: float,
    technical: float = 0.85,
    latency_ms: float = 100,
    peak_ram_mb: float = 256,
    peak_vram_mb: float = 0,
    provider_calls: int = 0,
    cost_usd: float = 0,
    manual_review: bool = False,
    fail_closed: bool = False,
    exact_text: float | None = 1.0,
    object_fidelity: float | None = 0.90,
    metadata: dict[str, object] | None = None,
) -> BenchmarkCaseResult:
    return BenchmarkCaseResult(
        case_id="smoke_pair-1",
        pair_id="pair-1",
        artwork_identity="art-1",
        success=True,
        semantic=SemanticMetrics(
            exact_text=exact_text,
            layout=semantic,
            object_fidelity=object_fidelity,
            color=semantic,
            texture=semantic,
            missing_detail=detail,
        ),
        technical=TechnicalMetrics(
            edge=technical,
            blur=technical,
            effective_resolution=technical,
            small_detail_survival=detail,
        ),
        operational=OperationalMetrics(
            latency_ms=latency_ms,
            peak_ram_mb=peak_ram_mb,
            peak_vram_mb=peak_vram_mb,
            provider_calls=provider_calls,
            cost_usd=cost_usd,
            manual_review=manual_review,
        ),
        precision=PrecisionEvidence(
            super_resolution_readiness=True,
            super_resolution_fail_closed=fail_closed,
        ),
        semantic_score=semantic,
        technical_score=technical,
        quality_score=quality,
        cohorts=["sr"],
        metadata=metadata or {},
    )


def _scorecard(
    lane: SRBenchmarkLane,
    result: BenchmarkCaseResult,
) -> BenchmarkScorecard:
    return BenchmarkScorecard(
        run_id=f"run-{lane.value}",
        dataset_id="dataset-1",
        tier=BenchmarkTier.SMOKE,
        recipe_id=f"sr-test-v1__sr_{lane.value}",
        recipe_version="1",
        status=HarnessRunStatus.COMPLETE,
        case_count=1,
        success_count=1,
        failure_count=0,
        manual_review_count=int(result.operational.manual_review),
        quality_mean=result.quality_score,
        semantic_mean=result.semantic_score,
        technical_mean=result.technical_score,
        failure_rate=0,
        manual_review_rate=float(result.operational.manual_review),
        latency_p50_ms=result.operational.latency_ms,
        latency_p95_ms=result.operational.latency_ms,
        provider_calls=result.operational.provider_calls,
        total_cost_usd=result.operational.cost_usd,
        provenance=RunProvenance(
            execution_kind=f"sr_benchmark_manifest:{lane.value}",
            quality_mode=QualityMode.PRINT_READY,
            dataset_manifest_sha256="dataset-fingerprint",
        ),
    )


def _run_matrix(
    tmp_path: Path,
    monkeypatch,
    *,
    results: dict[SRBenchmarkLane, BenchmarkCaseResult],
    lanczos: bool = False,
    local: bool = True,
    remote: bool = True,
    max_latency_ratio: float | None = None,
    max_cost_per_case_usd: float | None = None,
):
    settings = Settings(data_root=tmp_path / "runtime")
    store = HarnessStore(settings.harness_dir)
    runner = SRBenchmarkMatrixRunner(settings, object(), store)
    recipe_path = _recipe(tmp_path / "recipe.json")

    def fake_run_manifest_lane(*, lane, spec, recipe, manifest_path):
        result = results[lane]
        return _scorecard(lane, result), [result]

    monkeypatch.setattr(
        runner,
        "_run_manifest_lane",
        fake_run_manifest_lane,
    )
    spec = SRBenchmarkSpec(
        matrix_id="sr-matrix-test",
        dataset_id="dataset-1",
        tier=BenchmarkTier.SMOKE,
        recipe_path=str(recipe_path),
        native_manifest_path=str(tmp_path / "native.json"),
        lanczos_manifest_path=(
            str(tmp_path / "lanczos.json")
            if lanczos
            else None
        ),
        local_sr_manifest_path=(
            str(tmp_path / "local.json")
            if local
            else None
        ),
        remote_sr_manifest_path=(
            str(tmp_path / "remote.json")
            if remote
            else None
        ),
        quality_mode=QualityMode.PRINT_READY,
        min_quality_gain=0.01,
        min_detail_gain=0.03,
        max_semantic_drop=0.02,
        max_latency_ratio=max_latency_ratio,
        max_cost_per_case_usd=max_cost_per_case_usd,
    )
    return runner.run(spec, spec_base=tmp_path), store


def test_local_sr_wins_when_quality_and_detail_both_improve(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report, store = _run_matrix(
        tmp_path,
        monkeypatch,
        results={
            SRBenchmarkLane.NATIVE: _result(
                quality=0.80,
                detail=0.50,
                semantic=0.90,
            ),
            SRBenchmarkLane.LOCAL_SR: _result(
                quality=0.86,
                detail=0.64,
                semantic=0.90,
                latency_ms=160,
                peak_ram_mb=1024,
            ),
            SRBenchmarkLane.REMOTE_SR: _result(
                quality=0.84,
                detail=0.60,
                semantic=0.90,
                latency_ms=280,
                provider_calls=1,
                cost_usd=0.04,
            ),
        },
    )

    assert (
        report.recommendation
        is SRBenchmarkRecommendation.LOCAL_SR_FOR_HUMAN_REVIEW
    )
    assert report.local_sr_preferred_count == 1
    assert report.remote_sr_preferred_count == 0
    assert report.production_execution_enabled is False
    comparison = report.comparisons[0]
    assert comparison.preferred_lane is SRCasePreference.LOCAL_SR
    assert comparison.quality_gain == pytest.approx(0.06)
    assert comparison.detail_gain == pytest.approx(0.14)
    local_run = next(
        run for run in report.runs if run.lane is SRBenchmarkLane.LOCAL_SR
    )
    assert local_run.peak_ram_mb == 1024
    assert (
        store.sr_matrix_dir(report.matrix_id) / "report.json"
    ).is_file()


def test_missing_sr_backends_are_unavailable_not_fake_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report, _ = _run_matrix(
        tmp_path,
        monkeypatch,
        results={
            SRBenchmarkLane.NATIVE: _result(
                quality=0.82,
                detail=0.55,
                semantic=0.91,
            ),
        },
        local=False,
        remote=False,
    )

    assert (
        report.recommendation
        is SRBenchmarkRecommendation.INSUFFICIENT_EVIDENCE
    )
    unavailable = [run for run in report.runs if not run.available]
    assert {run.lane for run in unavailable} == {
        SRBenchmarkLane.LOCAL_SR,
        SRBenchmarkLane.REMOTE_SR,
    }
    assert report.production_execution_enabled is False
    assert any("local_sr unavailable" in reason for reason in report.reasons)


def test_semantic_regression_blocks_high_scoring_sr_challenger(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report, _ = _run_matrix(
        tmp_path,
        monkeypatch,
        results={
            SRBenchmarkLane.NATIVE: _result(
                quality=0.80,
                detail=0.50,
                semantic=0.92,
                object_fidelity=0.95,
            ),
            SRBenchmarkLane.LOCAL_SR: _result(
                quality=0.91,
                detail=0.80,
                semantic=0.82,
                object_fidelity=0.80,
            ),
        },
        remote=False,
    )

    comparison = report.comparisons[0]
    assert comparison.preferred_lane is SRCasePreference.NATIVE
    local = next(
        lane
        for lane in comparison.lanes
        if lane.lane is SRBenchmarkLane.LOCAL_SR
    )
    assert local.hallucination_risk is True
    assert any(
        "semantic hallucination-risk signal detected" in reason
        for reason in local.reasons
    )
    assert report.recommendation is SRBenchmarkRecommendation.KEEP_NATIVE


def test_fail_closed_or_manual_review_sr_is_not_promoted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report, _ = _run_matrix(
        tmp_path,
        monkeypatch,
        results={
            SRBenchmarkLane.NATIVE: _result(
                quality=0.80,
                detail=0.50,
                semantic=0.90,
            ),
            SRBenchmarkLane.LOCAL_SR: _result(
                quality=0.90,
                detail=0.75,
                semantic=0.91,
                fail_closed=True,
            ),
            SRBenchmarkLane.REMOTE_SR: _result(
                quality=0.91,
                detail=0.76,
                semantic=0.91,
                manual_review=True,
                provider_calls=1,
                cost_usd=0.05,
            ),
        },
    )

    assert report.native_preferred_count == 1
    comparison = report.comparisons[0]
    assert comparison.preferred_lane is SRCasePreference.NATIVE
    local = next(
        lane
        for lane in comparison.lanes
        if lane.lane is SRBenchmarkLane.LOCAL_SR
    )
    remote = next(
        lane
        for lane in comparison.lanes
        if lane.lane is SRBenchmarkLane.REMOTE_SR
    )
    assert local.fail_closed is True
    assert remote.manual_review is True


def test_remote_sr_wins_and_preserves_operational_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report, _ = _run_matrix(
        tmp_path,
        monkeypatch,
        results={
            SRBenchmarkLane.NATIVE: _result(
                quality=0.81,
                detail=0.52,
                semantic=0.90,
                latency_ms=100,
            ),
            SRBenchmarkLane.REMOTE_SR: _result(
                quality=0.88,
                detail=0.70,
                semantic=0.90,
                latency_ms=220,
                peak_ram_mb=768,
                peak_vram_mb=512,
                provider_calls=1,
                cost_usd=0.03,
            ),
        },
        local=False,
        max_latency_ratio=3.0,
        max_cost_per_case_usd=0.05,
    )

    assert (
        report.recommendation
        is SRBenchmarkRecommendation.REMOTE_SR_FOR_HUMAN_REVIEW
    )
    assert report.remote_sr_preferred_count == 1
    remote_run = next(
        run for run in report.runs if run.lane is SRBenchmarkLane.REMOTE_SR
    )
    assert remote_run.provider_calls == 1
    assert remote_run.total_cost_usd == pytest.approx(0.03)
    assert remote_run.peak_ram_mb == 768
    assert remote_run.peak_vram_mb == 512


def test_latency_or_cost_ceiling_can_reject_sr_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report, _ = _run_matrix(
        tmp_path,
        monkeypatch,
        results={
            SRBenchmarkLane.NATIVE: _result(
                quality=0.80,
                detail=0.50,
                semantic=0.90,
                latency_ms=100,
            ),
            SRBenchmarkLane.REMOTE_SR: _result(
                quality=0.90,
                detail=0.75,
                semantic=0.91,
                latency_ms=500,
                provider_calls=1,
                cost_usd=0.20,
            ),
        },
        local=False,
        max_latency_ratio=2.0,
        max_cost_per_case_usd=0.05,
    )

    comparison = report.comparisons[0]
    assert comparison.preferred_lane is SRCasePreference.NATIVE
    remote = next(
        lane
        for lane in comparison.lanes
        if lane.lane is SRBenchmarkLane.REMOTE_SR
    )
    assert any(
        "latency ratio exceeds benchmark ceiling" in reason
        for reason in remote.reasons
    )
    assert any(
        "cost per case exceeds benchmark ceiling" in reason
        for reason in remote.reasons
    )


def test_sr_matrix_requires_shared_dataset_fingerprint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(data_root=tmp_path / "runtime")
    runner = SRBenchmarkMatrixRunner(
        settings,
        object(),
        HarnessStore(settings.harness_dir),
    )
    recipe_path = _recipe(tmp_path / "recipe.json")
    native = _result(quality=0.80, detail=0.50, semantic=0.90)
    local = _result(quality=0.86, detail=0.65, semantic=0.90)

    def fake_run_manifest_lane(*, lane, spec, recipe, manifest_path):
        result = native if lane is SRBenchmarkLane.NATIVE else local
        scorecard = _scorecard(lane, result)
        if lane is SRBenchmarkLane.LOCAL_SR:
            scorecard.provenance.dataset_manifest_sha256 = "different"
        return scorecard, [result]

    monkeypatch.setattr(
        runner,
        "_run_manifest_lane",
        fake_run_manifest_lane,
    )
    spec = SRBenchmarkSpec(
        dataset_id="dataset-1",
        tier=BenchmarkTier.SMOKE,
        recipe_path=str(recipe_path),
        native_manifest_path=str(tmp_path / "native.json"),
        local_sr_manifest_path=str(tmp_path / "local.json"),
    )

    with pytest.raises(ValueError, match="shared dataset manifest fingerprint"):
        runner.run(spec, spec_base=tmp_path)


def test_lanczos_becomes_fair_sr_baseline_when_same_cohort(
    tmp_path: Path,
    monkeypatch,
) -> None:
    shared = {
        "sr_cohort": {
            "cohort_id": "cohort-1",
            "source_input_sha256": "a" * 64,
        }
    }
    report, _ = _run_matrix(
        tmp_path,
        monkeypatch,
        lanczos=True,
        remote=False,
        results={
            SRBenchmarkLane.NATIVE: _result(
                quality=0.80,
                detail=0.50,
                semantic=0.90,
                metadata=shared,
            ),
            SRBenchmarkLane.LANCZOS: _result(
                quality=0.84,
                detail=0.60,
                semantic=0.90,
                metadata=shared,
            ),
            SRBenchmarkLane.LOCAL_SR: _result(
                quality=0.85,
                detail=0.62,
                semantic=0.90,
                metadata=shared,
            ),
        },
    )

    comparison = report.comparisons[0]
    assert comparison.preferred_lane is SRCasePreference.LANCZOS
    assert report.lanczos_preferred_count == 1
    assert report.recommendation is SRBenchmarkRecommendation.KEEP_LANCZOS
    local = next(
        item
        for item in comparison.lanes
        if item.lane is SRBenchmarkLane.LOCAL_SR
    )
    assert "small-detail gain below promotion floor" in local.reasons


def test_pre_sr_cohort_mismatch_blocks_sr_promotion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report, _ = _run_matrix(
        tmp_path,
        monkeypatch,
        remote=False,
        results={
            SRBenchmarkLane.NATIVE: _result(
                quality=0.80,
                detail=0.50,
                semantic=0.90,
                metadata={
                    "sr_cohort": {
                        "cohort_id": "cohort-a",
                        "source_input_sha256": "a" * 64,
                    }
                },
            ),
            SRBenchmarkLane.LOCAL_SR: _result(
                quality=0.95,
                detail=0.90,
                semantic=0.95,
                metadata={
                    "sr_cohort": {
                        "cohort_id": "cohort-a",
                        "source_input_sha256": "b" * 64,
                    }
                },
            ),
        },
    )

    comparison = report.comparisons[0]
    assert comparison.preferred_lane is SRCasePreference.NATIVE
    local = next(
        item
        for item in comparison.lanes
        if item.lane is SRBenchmarkLane.LOCAL_SR
    )
    assert "pre-SR cohort source mismatch" in local.reasons
