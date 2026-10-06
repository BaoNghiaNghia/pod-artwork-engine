from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pod_artwork_engine.harness import HarnessStore
from pod_artwork_engine.harness_models import (
    BenchmarkTier,
    MaterialSeparationPolicyProposal,
    MaterialSeparationPolicyRecommendation,
)
from pod_artwork_engine.policy_activation import (
    PolicyActivationReadinessBuilder,
    PolicyActivationReadinessError,
    PolicyActivationReadinessStatus,
)
from pod_artwork_engine.policy_review import (
    PolicyDecision,
    PolicyDecisionReceiptBuilder,
    PolicyReviewKind,
    PolicyReviewPacketBuilder,
)


def _review_chain(
    tmp_path: Path,
    *,
    decision: PolicyDecision = PolicyDecision.APPROVE,
    packet_id: str = "review-material",
) -> tuple[HarnessStore, Path, Path, Path]:
    store = HarnessStore(tmp_path / "harness")
    proposal_path = tmp_path / f"{packet_id}-proposal.json"
    store.save_model(
        proposal_path,
        MaterialSeparationPolicyProposal(
            proposal_id=f"{packet_id}-policy",
            experiment_id=f"{packet_id}-experiment",
            dataset_id="golden-dataset",
            tier=BenchmarkTier.GOLDEN,
            dataset_manifest_sha256="golden-sha-123",
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
    packet_path = store.policy_review_dir(packet.packet_id) / "review-packet.json"
    PolicyDecisionReceiptBuilder(store).build(
        packet_path,
        decision,
        reviewer="human-reviewer",
        note="Golden evidence reviewed",
    )
    receipt_path = store.policy_decision_dir(packet.packet_id) / "decision-receipt.json"
    return store, proposal_path, packet_path, receipt_path


def test_approved_chain_is_ready_but_never_activates_production(tmp_path: Path) -> None:
    store, proposal_path, packet_path, receipt_path = _review_chain(tmp_path)

    report = PolicyActivationReadinessBuilder(store).build(
        receipt_path,
        assessment_id="activation-ready-1",
    )

    assert (
        report.status
        is PolicyActivationReadinessStatus.READY_FOR_EXPLICIT_ACTIVATION
    )
    assert report.eligible_for_explicit_activation is True
    assert report.requires_explicit_activation_confirmation is True
    assert report.automatically_applied is False
    assert report.production_execution_enabled is False
    assert report.blockers == []
    assert report.receipt_sha256 == hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    assert report.packet_sha256 == hashlib.sha256(packet_path.read_bytes()).hexdigest()
    assert report.proposal_sha256 == hashlib.sha256(
        proposal_path.read_bytes()
    ).hexdigest()

    persisted = (
        store.policy_activation_readiness_dir("activation-ready-1")
        / "readiness.json"
    )
    assert persisted.is_file()


def test_rejected_human_decision_is_blocked_from_activation(tmp_path: Path) -> None:
    store, _, _, receipt_path = _review_chain(
        tmp_path,
        decision=PolicyDecision.REJECT,
        packet_id="review-reject",
    )

    report = PolicyActivationReadinessBuilder(store).build(receipt_path)

    assert report.status is PolicyActivationReadinessStatus.BLOCKED
    assert report.eligible_for_explicit_activation is False
    assert "policy_activation_requires_approved_decision" in report.blockers
    assert "policy_activation_requires_human_approval_record" in report.blockers
    assert report.production_execution_enabled is False


def test_copied_receipt_outside_canonical_harness_path_is_blocked(
    tmp_path: Path,
) -> None:
    store, _, _, receipt_path = _review_chain(
        tmp_path,
        packet_id="review-copied-receipt",
    )
    copied_receipt = tmp_path / "copied-decision-receipt.json"
    copied_receipt.write_bytes(receipt_path.read_bytes())

    report = PolicyActivationReadinessBuilder(store).build(copied_receipt)

    assert report.status is PolicyActivationReadinessStatus.BLOCKED
    assert "policy_activation_receipt_not_canonical" in report.blockers
    assert report.production_execution_enabled is False


def test_packet_changed_after_human_decision_is_blocked(tmp_path: Path) -> None:
    store, _, packet_path, receipt_path = _review_chain(
        tmp_path,
        packet_id="review-packet-tamper",
    )
    packet_path.write_text(
        packet_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    report = PolicyActivationReadinessBuilder(store).build(receipt_path)

    assert report.status is PolicyActivationReadinessStatus.BLOCKED
    assert "policy_activation_review_packet_fingerprint_mismatch" in report.blockers
    assert report.production_execution_enabled is False


def test_proposal_changed_after_human_decision_is_blocked(tmp_path: Path) -> None:
    store, proposal_path, _, receipt_path = _review_chain(
        tmp_path,
        packet_id="review-proposal-tamper",
    )
    proposal_path.write_text(
        proposal_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    report = PolicyActivationReadinessBuilder(store).build(receipt_path)

    assert report.status is PolicyActivationReadinessStatus.BLOCKED
    assert "policy_activation_proposal_fingerprint_mismatch" in report.blockers
    assert report.production_execution_enabled is False


def test_activation_readiness_assessment_id_cannot_be_overwritten(
    tmp_path: Path,
) -> None:
    store, _, _, receipt_path = _review_chain(
        tmp_path,
        packet_id="review-immutable-assessment",
    )
    builder = PolicyActivationReadinessBuilder(store)
    builder.build(receipt_path, assessment_id="fixed-assessment")

    with pytest.raises(
        PolicyActivationReadinessError,
        match="readiness assessment already exists",
    ):
        builder.build(receipt_path, assessment_id="fixed-assessment")
