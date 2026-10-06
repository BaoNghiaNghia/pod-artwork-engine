from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import Field

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


class PolicyReviewKind(StrEnum):
    REGISTRATION = "registration"
    MATERIAL_SEPARATION = "material_separation"
    SUPER_RESOLUTION = "super_resolution"


class PolicyReviewDisposition(StrEnum):
    PENDING_HUMAN_APPROVAL = "pending_human_approval"
    NOT_ELIGIBLE = "not_eligible"


class PolicyDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class PolicyReviewPacket(StrictModel):
    packet_id: str
    policy_kind: PolicyReviewKind
    disposition: PolicyReviewDisposition
    proposal_id: str
    proposal_path: str
    proposal_sha256: str
    experiment_id: str
    dataset_id: str
    dataset_manifest_sha256: str
    tier: BenchmarkTier
    recommendation: str
    sufficient_evidence: bool
    eligible_for_human_approval: bool
    source_run_ids: list[str]
    source_artifact_ids: list[str] = Field(default_factory=list)
    requires_human_approval: bool = True
    automatically_applied: bool = False
    production_execution_enabled: bool = False
    reasons: list[str]


class PolicyDecisionReceipt(StrictModel):
    receipt_id: str
    packet_id: str
    packet_path: str
    packet_sha256: str
    policy_kind: PolicyReviewKind
    proposal_id: str
    proposal_path: str
    proposal_sha256: str
    decision: PolicyDecision
    reviewer: str
    note: str = ""
    human_approval_recorded: bool = False
    requires_separate_activation: bool = True
    automatically_applied: bool = False
    production_execution_enabled: bool = False
    reasons: list[str]
    created_at: datetime


class PolicyReviewPacketError(RuntimeError):
    pass


class PolicyDecisionReceiptError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PolicyReviewPacketBuilder:
    """Build an immutable, inert human-review packet from a Golden policy proposal.

    This boundary intentionally stops before approval or production activation.
    A packet can prove exactly which proposal was reviewed, but cannot change
    production execution by itself.
    """

    def __init__(self, store: HarnessStore) -> None:
        self.store = store

    def build(
        self,
        policy_kind: PolicyReviewKind,
        proposal_path: Path,
        *,
        packet_id: str | None = None,
    ) -> PolicyReviewPacket:
        proposal_path = proposal_path.expanduser().resolve()
        if not proposal_path.is_file():
            raise FileNotFoundError(f"policy proposal not found: {proposal_path}")

        source_run_ids: list[str] = []
        source_artifact_ids: list[str] = []
        if policy_kind is PolicyReviewKind.REGISTRATION:
            proposal = ProductionRegistrationPolicyProposal.model_validate_json(
                proposal_path.read_text(encoding="utf-8")
            )
            candidate = (
                proposal.recommendation
                is ProductionRegistrationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
            )
            source_run_ids = list(proposal.source_registration_run_ids)
            if proposal.cohort_id:
                source_artifact_ids.append(proposal.cohort_id)
            if proposal.matrix_id:
                source_artifact_ids.append(proposal.matrix_id)
        elif policy_kind is PolicyReviewKind.MATERIAL_SEPARATION:
            proposal = MaterialSeparationPolicyProposal.model_validate_json(
                proposal_path.read_text(encoding="utf-8")
            )
            candidate = (
                proposal.recommendation
                is MaterialSeparationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
            )
            source_run_ids = [proposal.source_run_id]
            if proposal.cohort_id:
                source_artifact_ids.append(proposal.cohort_id)
            if proposal.matrix_id:
                source_artifact_ids.append(proposal.matrix_id)
        elif policy_kind is PolicyReviewKind.SUPER_RESOLUTION:
            proposal = SRPolicyProposal.model_validate_json(
                proposal_path.read_text(encoding="utf-8")
            )
            candidate = (
                proposal.recommendation is not SRPolicyRecommendation.MANUAL_REVIEW
            )
            source_artifact_ids = [proposal.cohort_id]
        else:  # pragma: no cover - StrEnum keeps CLI/API callers inside known values.
            raise PolicyReviewPacketError(f"unsupported policy kind: {policy_kind}")

        blockers: list[str] = []
        if proposal.tier is not BenchmarkTier.GOLDEN:
            blockers.append("policy_review_requires_golden_holdout")
        if not proposal.dataset_manifest_sha256:
            blockers.append("policy_review_requires_dataset_fingerprint")
        if not proposal.sufficient_evidence:
            blockers.append("policy_review_requires_sufficient_evidence")
        if not candidate:
            blockers.append("policy_recommendation_is_not_human_approval_candidate")
        if not proposal.requires_human_approval:
            blockers.append("policy_proposal_missing_human_approval_boundary")
        if proposal.automatically_applied:
            blockers.append("policy_proposal_was_automatically_applied")
        if proposal.production_execution_enabled:
            blockers.append("policy_proposal_already_enables_production_execution")

        eligible = not blockers
        packet_id = packet_id or "policy_review_" + uuid4().hex
        disposition = (
            PolicyReviewDisposition.PENDING_HUMAN_APPROVAL
            if eligible
            else PolicyReviewDisposition.NOT_ELIGIBLE
        )
        reasons = [
            "immutable human-review packet only",
            "packet creation cannot approve or activate a production policy",
            "production execution remains disabled",
            *blockers,
        ]
        if eligible:
            reasons.append(
                "Golden proposal passed review-packet admission gates and awaits explicit human approval"
            )

        packet = PolicyReviewPacket(
            packet_id=packet_id,
            policy_kind=policy_kind,
            disposition=disposition,
            proposal_id=proposal.proposal_id,
            proposal_path=str(proposal_path),
            proposal_sha256=_sha256(proposal_path),
            experiment_id=proposal.experiment_id,
            dataset_id=proposal.dataset_id,
            dataset_manifest_sha256=proposal.dataset_manifest_sha256,
            tier=proposal.tier,
            recommendation=proposal.recommendation.value,
            sufficient_evidence=proposal.sufficient_evidence,
            eligible_for_human_approval=eligible,
            source_run_ids=source_run_ids,
            source_artifact_ids=source_artifact_ids,
            requires_human_approval=True,
            automatically_applied=False,
            production_execution_enabled=False,
            reasons=list(dict.fromkeys(reasons)),
        )
        output_dir = self.store.policy_review_dir(packet.packet_id)
        packet_path = output_dir / "review-packet.json"
        if packet_path.exists():
            raise PolicyReviewPacketError(
                f"policy review packet already exists: {packet.packet_id}"
            )
        self.store.save_model(packet_path, packet)
        return packet


class PolicyDecisionReceiptBuilder:
    """Record an explicit human decision without applying or activating policy."""

    def __init__(self, store: HarnessStore) -> None:
        self.store = store

    def build(
        self,
        packet_path: Path,
        decision: PolicyDecision,
        *,
        reviewer: str,
        note: str = "",
        receipt_id: str | None = None,
    ) -> PolicyDecisionReceipt:
        packet_path = packet_path.expanduser().resolve()
        if not packet_path.is_file():
            raise FileNotFoundError(f"policy review packet not found: {packet_path}")
        reviewer = reviewer.strip()
        if not reviewer:
            raise PolicyDecisionReceiptError("reviewer is required")

        packet = load_policy_review_packet(packet_path)
        blockers: list[str] = []
        if packet.disposition is not PolicyReviewDisposition.PENDING_HUMAN_APPROVAL:
            blockers.append("policy_review_packet_is_not_pending_human_approval")
        if not packet.eligible_for_human_approval:
            blockers.append("policy_review_packet_is_not_eligible")
        if not packet.requires_human_approval:
            blockers.append("policy_review_packet_missing_human_approval_boundary")
        if packet.automatically_applied:
            blockers.append("policy_review_packet_was_automatically_applied")
        if packet.production_execution_enabled:
            blockers.append("policy_review_packet_already_enables_production_execution")

        proposal_path = Path(packet.proposal_path).expanduser().resolve()
        if not proposal_path.is_file():
            blockers.append("policy_review_proposal_missing")
        elif _sha256(proposal_path) != packet.proposal_sha256:
            blockers.append("policy_review_proposal_fingerprint_mismatch")

        if blockers:
            raise PolicyDecisionReceiptError(", ".join(blockers))

        decision_dir = self.store.policy_decision_dir(packet.packet_id)
        receipt_path = decision_dir / "decision-receipt.json"
        if receipt_path.exists():
            raise PolicyDecisionReceiptError(
                f"policy decision already recorded for packet: {packet.packet_id}"
            )

        human_approval_recorded = decision is PolicyDecision.APPROVE
        reasons = [
            "explicit human policy decision receipt",
            "decision receipt is cryptographically bound to the immutable review packet",
            "decision receipt does not activate production execution",
            "separate explicit activation remains required",
        ]
        if human_approval_recorded:
            reasons.append("human approved the reviewed policy proposal")
        else:
            reasons.append("human rejected the reviewed policy proposal")

        receipt = PolicyDecisionReceipt(
            receipt_id=receipt_id or "policy_decision_" + uuid4().hex,
            packet_id=packet.packet_id,
            packet_path=str(packet_path),
            packet_sha256=_sha256(packet_path),
            policy_kind=packet.policy_kind,
            proposal_id=packet.proposal_id,
            proposal_path=str(proposal_path),
            proposal_sha256=packet.proposal_sha256,
            decision=decision,
            reviewer=reviewer,
            note=note.strip(),
            human_approval_recorded=human_approval_recorded,
            requires_separate_activation=True,
            automatically_applied=False,
            production_execution_enabled=False,
            reasons=reasons,
            created_at=datetime.now(timezone.utc),
        )
        self.store.save_model(receipt_path, receipt)
        return receipt


def load_policy_review_packet(path: Path) -> PolicyReviewPacket:
    return PolicyReviewPacket.model_validate_json(path.read_text(encoding="utf-8"))


def load_policy_decision_receipt(path: Path) -> PolicyDecisionReceipt:
    return PolicyDecisionReceipt.model_validate_json(path.read_text(encoding="utf-8"))
