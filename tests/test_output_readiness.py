from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from pod_artwork_engine.api import create_app
from pod_artwork_engine.contracts import (
    ArtworkType, BoundingBox, DesignSpec, JobRecord, JobState,
    MaterialSeparationDisposition, MaterialSeparationEvidence, QualityMode,
)
from pod_artwork_engine.engine import Engine
from pod_artwork_engine.job_store import JobStore
from pod_artwork_engine.output_readiness import artwork_output_blockers
from pod_artwork_engine.reconstruction import CandidateInfo
from pod_artwork_engine.settings import Settings


def _source(path: Path, *, transparent: bool = False) -> Path:
    if transparent:
        image = Image.new("RGBA", (500, 500), (0, 0, 0, 0))
    else:
        image = Image.new("RGB", (500, 500), (248, 248, 248))
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 80, 420, 430), fill=(190, 190, 190))
    draw.text((185, 210), "DESIGN", fill=(16, 20, 30))
    image.save(path)
    return path


def test_output_readiness_blocks_unverified_local_crop(tmp_path: Path) -> None:
    source = _source(tmp_path / "sweatshirt.png")
    candidate = CandidateInfo(
        path=source, source_path=source, native_width=500,
        native_height=500, alpha_method="border_color_soft_mask", local_baseline=True,
    )
    material = MaterialSeparationEvidence(
        disposition=MaterialSeparationDisposition.MANUAL_REVIEW,
        primary_index=0, confidence=0.41, fail_closed=True,
    )
    spec = DesignSpec(
        artwork_type=ArtworkType.ILLUSTRATION,
        artwork_bbox=BoundingBox(x=0.15, y=0.16, width=0.7, height=0.7),
        required_capabilities=["need_semantic_reconstruction"],
    )
    assert "artwork_not_isolated_from_product_mockup" in artwork_output_blockers(
        spec, material, candidate, used_remote=False
    )
    assert artwork_output_blockers(
        spec, material, candidate.model_copy() if hasattr(candidate, "model_copy") else candidate,
        used_remote=True
    ) == []
    assert artwork_output_blockers(
        spec, material.model_copy(update={"fail_closed": False}), candidate, used_remote=False
    ) == []


def test_engine_does_not_export_mockup_when_separation_is_unverified(
    monkeypatch, tmp_path: Path
) -> None:
    source = _source(tmp_path / "product.jpg")
    settings = Settings(data_root=tmp_path / "data")
    engine = Engine(settings)
    spec = DesignSpec(
        artwork_type=ArtworkType.ILLUSTRATION,
        artwork_bbox=BoundingBox(x=0.15, y=0.16, width=0.70, height=0.70),
        confidence=0.5,
        required_capabilities=["need_semantic_reconstruction"],
    )
    material = MaterialSeparationEvidence(
        disposition=MaterialSeparationDisposition.MANUAL_REVIEW,
        primary_index=0, confidence=0.41, fail_closed=True,
    )
    monkeypatch.setattr(engine, "_analyze", lambda *args, **kwargs: (spec, None))
    monkeypatch.setattr(engine, "_material_separation_evidence", lambda *args, **kwargs: material)

    job = engine.create_job([source], QualityMode.PRINT_READY)
    result = engine.run_job(job.job_id)
    assert result.state is JobState.REVIEW_REQUIRED
    assert result.progress == 1.0
    assert result.result_path is None
    assert "artwork_not_isolated_from_product_mockup" in (result.failure_reason or "")
    assert not (settings.jobs_dir / job.job_id / "final" / "4500x5400.png").exists()
    assert engine.checkpoints.payload(job.job_id, "output_readiness")["print_artwork_isolated"] is False


def test_legacy_unisolated_mockup_cannot_be_exported_via_api(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "data")
    settings.ensure_directories()
    output = tmp_path / "mockup.png"
    Image.new("RGBA", (40, 40), (200, 200, 200, 255)).save(output)
    job = JobRecord(
        state=JobState.REVIEW_REQUIRED,
        result_path=str(output),
        failure_reason="semantic_provider_not_used",
    )
    JobStore(settings.database_path).save(job)
    response = TestClient(create_app(settings)).get(f"/jobs/{job.job_id}/output")
    assert response.status_code == 409
    assert "not isolated" in response.json()["detail"]
