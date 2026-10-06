from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from .harness import HarnessStore
from .harness_models import (
    BenchmarkTier,
    MaterialSeparationPolicyProposal,
    MaterialSeparationPolicyRecommendation,
    ProductionRegistrationPolicyProposal,
    ProductionRegistrationPolicyRecommendation,
    SRPolicyProposal,
    SRPolicyRecommendation,
    StrictModel,
)
from .policy_review import (
    PolicyDecision,
    PolicyDecisionReceipt,
    PolicyReviewDisposition,
    PolicyReviewKind,
    PolicyReviewPacket,
    load_policy_decision_receipt,
    load_policy_review_packet,
)


class PolicyActivationReadinessStatus(StrEnum):
    READY_FOR_EXPLICIT_ACTIVATION = "ready_for_explicit_activation"
    BLOCKED = "blocked"


class PolicyActivationReadiness(StrictModel):
    assessment_id: str
    status: PolicyActivationReadinessStatus
    eligible_for_explicit_activation: bool
    policy_kind: PolicyReviewKind
    receipt_id: str
    receipt_path: str
    receipt_sha256: str
    packet_id: str
    packet_path: str
    packet_sha256: str
    proposal_id: str
    proposal_path: str
    proposal_sha256: str
    experiment_id: str | None = None
    dataset_id: str | None = None
    dataset_manifest_sha256: str | None = None
    tier: BenchmarkTier | None = None
    recommendation: str | None = None
    reviewer: str
    decision: PolicyDecision
    human_approval_recorded: bool
    requires_explicit_activation_confirmation: bool = True
    automatically_applied: bool = False
    production_execution_enabled: bool = False
    blockers: list[str]
    reasons: list[str]
    created_at: datetime


class PolicyActivationReadinessError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PolicyActivationReadinessBuilder:
    """Re-verify a complete human-governed policy chain without activating it."""

    def __init__(self, store: HarnessStore) -> None:
        self.store = store

    def build(
        self,
        receipt_path: Path,
        *,
        assessment_id: str | None = None,
    ) -> PolicyActivationReadiness:
        receipt_path = receipt_path.expanduser().resolve()
        if not receipt_path.is_file():
            raise FileNotFoundError(f"policy decision receipt not found: {receipt_path}")

        receipt = load_policy_decision_receipt(receipt_path)
        blockers: list[str] = []

        canonical_receipt_path = (
            self.store.policy_decision_dir(receipt.packet_id) / "decision-receipt.json"
        ).resolve()
        if receipt_path != canonical_receipt_path:
            blockers.append("policy_activation_receipt_not_canonical")

        if receipt.decision is not PolicyDecision.APPROVE:
            blockers.append("policy_activation_requires_approved_decision")
        if not receipt.human_approval_recorded:
            blockers.append("policy_activation_requires_human_approval_record")
        if not receipt.reviewer.strip():
            blockers.append("policy_activation_requires_reviewer_identity")
        if not receipt.requires_separate_activation:
            blockers.append("policy_activation_receipt_missing_separate_activation_boundary")
        if receipt.automatically_applied:
            blockers.append("policy_activation_receipt_was_automatically_applied")
        if receipt.production_execution_enabled:
            blockers.append("policy_activation_receipt_already_enables_production_execution")

        packet_path = Path(receipt.packet_path).expanduser().resolve()
        canonical_packet_path = (
            self.store.policy_review_dir(receipt.packet_id) / "review-packet.json"
        ).resolve()
        if packet_path != canonical_packet_path:
            blockers.append("policy_activation_review_packet_not_canonical")

        packet: PolicyReviewPacket | None = None
        if not packet_path.is_file():
            blockers.append("policy_activation_review_packet_missing")
        else:
            actual_packet_sha256 = _sha256(packet_path)
            if actual_packet_sha256 != receipt.packet_sha256:
                blockers.append("policy_activation_review_packet_fingerprint_mismatch")
            try:
                packet = load_policy_review_packet(packet_path)
            except Exception:
                blockers.append("policy_activation_review_packet_invalid")

        proposal_path = Path(receipt.proposal_path).expanduser().resolve()
        proposal = None
        if not proposal_path.is_file():
            blockers.append("policy_activation_proposal_missing")
        else:
            actual_proposal_sha256 = _sha256(proposal_path)
            if actual_proposal_sha256 != receipt.proposal_sha256:
                blockers.append("policy_activation_proposal_fingerprint_mismatch")
            try:
                proposal = self._load_proposal(receipt.policy_kind, proposal_path)
            except Exception:
                blockers.append("policy_activation_proposal_invalid")

        if packet is not None:
            self._validate_packet(receipt, packet, blockers)
        if packet is not None and proposal is not None:
            self._validate_proposal(receipt, packet, proposal, blockers)

        eligible = not blockers
        status = (
            PolicyActivationReadinessStatus.READY_FOR_EXPLICIT_ACTIVATION
            if eligible
            else PolicyActivationReadinessStatus.BLOCKED
        )

        assessment_id = assessment_id or "policy_activation_readiness_" + uuid4().hex
        output_dir = self.store.policy_activation_readiness_dir(assessment_id)
        output_path = output_dir / "readiness.json"
        if output_path.exists():
            raise PolicyActivationReadinessError(
                f"policy activation readiness assessment already exists: {assessment_id}"
            )

        experiment_id = packet.experiment_id if packet is not None else None
        dataset_id = packet.dataset_id if packet is not None else None
        dataset_manifest_sha256 = (
            packet.dataset_manifest_sha256 if packet is not None else None
        )
        tier = packet.tier if packet is not None else None
        recommendation = packet.recommendation if packet is not None else None

        reasons = [
            "activation readiness dry-run only",
            "assessment does not modify production policy or execution",
            "production execution remains disabled",
            "a separate explicit activation confirmation and implementation step is still required",
        ]
        if eligible:
            reasons.append(
                "approved Golden policy chain passed readiness checks for a future explicit activation step"
            )
        else:
            reasons.append("activation readiness is blocked until all blockers are resolved")

        report = PolicyActivationReadiness(
            assessment_id=assessment_id,
            status=status,
            eligible_for_explicit_activation=eligible,
            policy_kind=receipt.policy_kind,
            receipt_id=receipt.receipt_id,
            receipt_path=str(receipt_path),
            receipt_sha256=_sha256(receipt_path),
            packet_id=receipt.packet_id,
            packet_path=str(packet_path),
            packet_sha256=receipt.packet_sha256,
            proposal_id=receipt.proposal_id,
            proposal_path=str(proposal_path),
            proposal_sha256=receipt.proposal_sha256,
            experiment_id=experiment_id,
            dataset_id=dataset_id,
            dataset_manifest_sha256=dataset_manifest_sha256,
            tier=tier,
            recommendation=recommendation,
            reviewer=receipt.reviewer,
            decision=receipt.decision,
            human_approval_recorded=receipt.human_approval_recorded,
            requires_explicit_activation_confirmation=True,
            automatically_applied=False,
            production_execution_enabled=False,
            blockers=list(dict.fromkeys(blockers)),
            reasons=reasons,
            created_at=datetime.now(timezone.utc),
        )
        self.store.save_model(output_path, report)
        return report

    @staticmethod
    def _load_proposal(policy_kind: PolicyReviewKind, path: Path):
        content = path.read_text(encoding="utf-8")
        if policy_kind is PolicyReviewKind.REGISTRATION:
            return ProductionRegistrationPolicyProposal.model_validate_json(content)
        if policy_kind is PolicyReviewKind.MATERIAL_SEPARATION:
            return MaterialSeparationPolicyProposal.model_validate_json(content)
        if policy_kind is PolicyReviewKind.SUPER_RESOLUTION:
            return SRPolicyProposal.model_validate_json(content)
        raise PolicyActivationReadinessError(f"unsupported policy kind: {policy_kind}")

    @staticmethod
    def _validate_packet(
        receipt: PolicyDecisionReceipt,
        packet: PolicyReviewPacket,
        blockers: list[str],
    ) -> None:
        if packet.packet_id != receipt.packet_id:
            blockers.append("policy_activation_packet_id_mismatch")
        if packet.policy_kind is not receipt.policy_kind:
            blockers.append("policy_activation_policy_kind_mismatch")
        if packet.proposal_id != receipt.proposal_id:
            blockers.append("policy_activation_packet_proposal_id_mismatch")
        if Path(packet.proposal_path).expanduser().resolve() != Path(
            receipt.proposal_path
        ).expanduser().resolve():
            blockers.append("policy_activation_packet_proposal_path_mismatch")
        if packet.proposal_sha256 != receipt.proposal_sha256:
            blockers.append("policy_activation_packet_proposal_fingerprint_mismatch")
        if packet.disposition is not PolicyReviewDisposition.PENDING_HUMAN_APPROVAL:
            blockers.append("policy_activation_packet_not_pending_human_approval")
        if not packet.eligible_for_human_approval:
            blockers.append("policy_activation_packet_not_eligible_for_human_approval")
        if packet.tier is not BenchmarkTier.GOLDEN:
            blockers.append("policy_activation_requires_golden_holdout")
        if not packet.dataset_manifest_sha256:
            blockers.append("policy_activation_requires_dataset_fingerprint")
        if not packet.sufficient_evidence:
            blockers.append("policy_activation_requires_sufficient_evidence")
        if not packet.requires_human_approval:
            blockers.append("policy_activation_packet_missing_human_approval_boundary")
        if packet.automatically_applied:
            blockers.append("policy_activation_packet_was_automatically_applied")
        if packet.production_execution_enabled:
            blockers.append("policy_activation_packet_already_enables_production_execution")

    @staticmethod
    def _validate_proposal(
        receipt: PolicyDecisionReceipt,
        packet: PolicyReviewPacket,
        proposal,
        blockers: list[str],
    ) -> None:
        if proposal.proposal_id != packet.proposal_id:
            blockers.append("policy_activation_proposal_id_mismatch")
        if proposal.experiment_id != packet.experiment_id:
            blockers.append("policy_activation_experiment_id_mismatch")
        if proposal.dataset_id != packet.dataset_id:
            blockers.append("policy_activation_dataset_id_mismatch")
        if proposal.dataset_manifest_sha256 != packet.dataset_manifest_sha256:
            blockers.append("policy_activation_dataset_fingerprint_mismatch")
        if proposal.tier is not BenchmarkTier.GOLDEN:
            blockers.append("policy_activation_proposal_requires_golden_holdout")
        if not proposal.sufficient_evidence:
            blockers.append("policy_activation_proposal_requires_sufficient_evidence")
        if not proposal.requires_human_approval:
            blockers.append("policy_activation_proposal_missing_human_approval_boundary")
        if proposal.automatically_applied:
            blockers.append("policy_activation_proposal_was_automatically_applied")
        if proposal.production_execution_enabled:
            blockers.append("policy_activation_proposal_already_enables_production_execution")

        candidate = False
        if receipt.policy_kind is PolicyReviewKind.REGISTRATION:
            candidate = (
                proposal.recommendation
                is ProductionRegistrationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
            )
        elif receipt.policy_kind is PolicyReviewKind.MATERIAL_SEPARATION:
            candidate = (
                proposal.recommendation
                is MaterialSeparationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
            )
        elif receipt.policy_kind is PolicyReviewKind.SUPER_RESOLUTION:
            candidate = proposal.recommendation is not SRPolicyRecommendation.MANUAL_REVIEW
        if not candidate:
            blockers.append("policy_activation_proposal_not_human_approval_candidate")
        if proposal.recommendation.value != packet.recommendation:
            blockers.append("policy_activation_recommendation_mismatch")


def load_policy_activation_readiness(path: Path) -> PolicyActivationReadiness:
    return PolicyActivationReadiness.model_validate_json(path.read_text(encoding="utf-8"))
