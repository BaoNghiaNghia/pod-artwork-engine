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
    SRPolicyProposal,
    SRPolicyRecommendation,
)
from pod_artwork_engine.policy_review import (
    PolicyDecision,
    PolicyDecisionReceiptBuilder,
    PolicyDecisionReceiptError,
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


def test_sr_policy_can_enter_same_human_review_boundary(tmp_path: Path) -> None:
    store = HarnessStore(tmp_path / "harness")
    proposal_path = _write_proposal(
        store,
        tmp_path / "sr-proposal.json",
        SRPolicyProposal(
            proposal_id="sr-policy-1",
            experiment_id="sr-experiment-1",
            dataset_id="golden-dataset",
            tier=BenchmarkTier.GOLDEN,
            dataset_manifest_sha256="sr123",
            cohort_id="sr-cohort-1",
            min_quality_gain=0.01,
            min_detail_gain=0.03,
            max_semantic_drop=0.02,
            local_backend_available=True,
            recommendation=SRPolicyRecommendation.LOCAL_SR,
            sufficient_evidence=True,
        ),
    )

    packet = PolicyReviewPacketBuilder(store).build(
        PolicyReviewKind.SUPER_RESOLUTION,
        proposal_path,
    )

    assert packet.disposition is PolicyReviewDisposition.PENDING_HUMAN_APPROVAL
    assert packet.eligible_for_human_approval is True
    assert packet.source_run_ids == []
    assert packet.source_artifact_ids == ["sr-cohort-1"]
    assert packet.production_execution_enabled is False


def _pending_material_review(
    tmp_path: Path,
    *,
    packet_id: str,
) -> tuple[HarnessStore, Path, Path]:
    store = HarnessStore(tmp_path / "harness")
    proposal_path = _write_proposal(
        store,
        tmp_path / f"{packet_id}-proposal.json",
        MaterialSeparationPolicyProposal(
            proposal_id=f"{packet_id}-policy",
            experiment_id=f"{packet_id}-experiment",
            dataset_id="golden-dataset",
            tier=BenchmarkTier.GOLDEN,
            dataset_manifest_sha256="abc123",
            source_run_id=f"{packet_id}-run",
            recommendation=(
                MaterialSeparationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
            ),
            sufficient_evidence=True,
        ),
    )
    packet = PolicyReviewPacketBuilder(store).build(
        PolicyReviewKind.MATERIAL_SEPARATION,
        proposal_path,
        packet_id=packet_id,
    )
    return (
        store,
        proposal_path,
        store.policy_review_dir(packet.packet_id) / "review-packet.json",
    )


def test_approval_receipt_records_human_decision_without_activation(
    tmp_path: Path,
) -> None:
    store, proposal_path, packet_path = _pending_material_review(
        tmp_path,
        packet_id="review-approve",
    )

    receipt = PolicyDecisionReceiptBuilder(store).build(
        packet_path,
        PolicyDecision.APPROVE,
        reviewer="test-reviewer",
        note="Golden evidence reviewed",
        receipt_id="receipt-approve",
    )

    assert receipt.human_approval_recorded is True
    assert receipt.requires_separate_activation is True
    assert receipt.automatically_applied is False
    assert receipt.production_execution_enabled is False
    assert receipt.proposal_sha256 == hashlib.sha256(
        proposal_path.read_bytes()
    ).hexdigest()
    assert receipt.packet_sha256 == hashlib.sha256(
        packet_path.read_bytes()
    ).hexdigest()

    persisted = (
        store.policy_decision_dir("review-approve") / "decision-receipt.json"
    )
    assert persisted.is_file()


def test_decision_receipt_fails_if_reviewed_proposal_changed(
    tmp_path: Path,
) -> None:
    store, proposal_path, packet_path = _pending_material_review(
        tmp_path,
        packet_id="review-tamper",
    )
    proposal_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(
        PolicyDecisionReceiptError,
        match="policy_review_proposal_fingerprint_mismatch",
    ):
        PolicyDecisionReceiptBuilder(store).build(
            packet_path,
            PolicyDecision.APPROVE,
            reviewer="test-reviewer",
        )


def test_only_one_human_decision_can_be_recorded_per_review_packet(
    tmp_path: Path,
) -> None:
    store, _, packet_path = _pending_material_review(
        tmp_path,
        packet_id="review-single-decision",
    )
    builder = PolicyDecisionReceiptBuilder(store)
    builder.build(
        packet_path,
        PolicyDecision.REJECT,
        reviewer="test-reviewer",
        note="Needs more evidence",
    )

    with pytest.raises(
        PolicyDecisionReceiptError,
        match="policy decision already recorded",
    ):
        builder.build(
            packet_path,
            PolicyDecision.APPROVE,
            reviewer="test-reviewer",
        )
