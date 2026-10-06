from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pod_artwork_engine.harness import HarnessStore
from pod_artwork_engine.harness_models import (
    BenchmarkTier,
    MaterialSeparationPolicyProposal,
    MaterialSeparationPolicyRecommendation,
    ProductionRegistrationPolicyProposal,
    ProductionRegistrationPolicyRecommendation,
    RegistrationPolicyRecommendation,
)
from pod_artwork_engine.policy_review import (
    PolicyReviewDisposition,
    PolicyReviewKind,
    PolicyReviewPacketBuilder,
    PolicyReviewPacketError,
)


def _write_proposal(store: HarnessStore, path: Path, proposal: object) -> Path:
    store.save_model(path, proposal)
    return path


def test_material_separation_candidate_becomes_pending_review_packet(
    tmp_path: Path,
) -> None:
    store = HarnessStore(tmp_path / "harness")
    proposal_path = _write_proposal(
        store,
        tmp_path / "material-proposal.json",
        MaterialSeparationPolicyProposal(
            proposal_id="material-policy-1",
            experiment_id="material-experiment-1",
            dataset_id="golden-dataset",
            tier=BenchmarkTier.GOLDEN,
            dataset_manifest_sha256="abc123",
            source_run_id="run-golden-1",
            recommendation=(
                MaterialSeparationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
            ),
            sufficient_evidence=True,
        ),
    )

    packet = PolicyReviewPacketBuilder(store).build(
        PolicyReviewKind.MATERIAL_SEPARATION,
        proposal_path,
        packet_id="review-material-1",
    )

    expected_hash = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
    assert packet.disposition is PolicyReviewDisposition.PENDING_HUMAN_APPROVAL
    assert packet.eligible_for_human_approval is True
    assert packet.proposal_sha256 == expected_hash
    assert packet.source_run_ids == ["run-golden-1"]
    assert packet.production_execution_enabled is False
    assert packet.automatically_applied is False

    persisted = store.policy_review_dir(packet.packet_id) / "review-packet.json"
    assert persisted.is_file()
    assert expected_hash in persisted.read_text(encoding="utf-8")


def test_unsafe_policy_proposal_is_not_eligible_for_human_approval(
    tmp_path: Path,
) -> None:
    store = HarnessStore(tmp_path / "harness")
    proposal_path = _write_proposal(
        store,
        tmp_path / "unsafe-material-proposal.json",
        MaterialSeparationPolicyProposal(
            proposal_id="material-policy-unsafe",
            experiment_id="material-experiment-unsafe",
            dataset_id="golden-dataset",
            tier=BenchmarkTier.GOLDEN,
            dataset_manifest_sha256="abc123",
            source_run_id="run-golden-unsafe",
            recommendation=(
                MaterialSeparationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
            ),
            sufficient_evidence=True,
            production_execution_enabled=True,
        ),
    )

    packet = PolicyReviewPacketBuilder(store).build(
        PolicyReviewKind.MATERIAL_SEPARATION,
        proposal_path,
    )

    assert packet.disposition is PolicyReviewDisposition.NOT_ELIGIBLE
    assert packet.eligible_for_human_approval is False
    assert packet.production_execution_enabled is False
    assert "policy_proposal_already_enables_production_execution" in packet.reasons


def test_registration_candidate_uses_same_inert_review_boundary(
    tmp_path: Path,
) -> None:
    store = HarnessStore(tmp_path / "harness")
    proposal_path = _write_proposal(
        store,
        tmp_path / "registration-proposal.json",
        ProductionRegistrationPolicyProposal(
            proposal_id="registration-policy-1",
            experiment_id="dewarp-experiment-1",
            dataset_id="golden-dataset",
            tier=BenchmarkTier.GOLDEN,
            dataset_manifest_sha256="def456",
            calibration_proposal_id="registration-calibration-1",
            calibration_recommendation=(
                RegistrationPolicyRecommendation.AFFINE_FOR_DEWARP_BENCHMARK
            ),
            source_registration_run_ids=["run-golden-a", "run-golden-b"],
            recommendation=(
                ProductionRegistrationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
            ),
            sufficient_evidence=True,
        ),
    )

    packet = PolicyReviewPacketBuilder(store).build(
        PolicyReviewKind.REGISTRATION,
        proposal_path,
    )

    assert packet.disposition is PolicyReviewDisposition.PENDING_HUMAN_APPROVAL
    assert packet.source_run_ids == ["run-golden-a", "run-golden-b"]
    assert packet.requires_human_approval is True
    assert packet.automatically_applied is False
    assert packet.production_execution_enabled is False


def test_review_packet_id_is_immutable_once_written(tmp_path: Path) -> None:
    store = HarnessStore(tmp_path / "harness")
    proposal_path = _write_proposal(
        store,
        tmp_path / "material-proposal.json",
        MaterialSeparationPolicyProposal(
            proposal_id="material-policy-2",
            experiment_id="material-experiment-2",
            dataset_id="golden-dataset",
            tier=BenchmarkTier.GOLDEN,
            dataset_manifest_sha256="abc123",
            source_run_id="run-golden-2",
            recommendation=(
                MaterialSeparationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
            ),
            sufficient_evidence=True,
        ),
    )

    builder = PolicyReviewPacketBuilder(store)
    builder.build(
        PolicyReviewKind.MATERIAL_SEPARATION,
        proposal_path,
        packet_id="fixed-review-id",
    )

    with pytest.raises(PolicyReviewPacketError, match="already exists"):
        builder.build(
            PolicyReviewKind.MATERIAL_SEPARATION,
            proposal_path,
            packet_id="fixed-review-id",
        )
