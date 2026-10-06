from __future__ import annotations

import hashlib
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
    StrictModel,
)


class PolicyReviewKind(StrEnum):
    REGISTRATION = "registration"
    MATERIAL_SEPARATION = "material_separation"


class PolicyReviewDisposition(StrEnum):
    PENDING_HUMAN_APPROVAL = "pending_human_approval"
    NOT_ELIGIBLE = "not_eligible"


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
    requires_human_approval: bool = True
    automatically_applied: bool = False
    production_execution_enabled: bool = False
    reasons: list[str]


class PolicyReviewPacketError(RuntimeError):
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

        if policy_kind is PolicyReviewKind.REGISTRATION:
            proposal = ProductionRegistrationPolicyProposal.model_validate_json(
                proposal_path.read_text(encoding="utf-8")
            )
            candidate = (
                proposal.recommendation
                is ProductionRegistrationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
            )
            source_run_ids = list(proposal.source_registration_run_ids)
        elif policy_kind is PolicyReviewKind.MATERIAL_SEPARATION:
            proposal = MaterialSeparationPolicyProposal.model_validate_json(
                proposal_path.read_text(encoding="utf-8")
            )
            candidate = (
                proposal.recommendation
                is MaterialSeparationPolicyRecommendation.CANDIDATE_FOR_HUMAN_APPROVAL
            )
            source_run_ids = [proposal.source_run_id]
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


def load_policy_review_packet(path: Path) -> PolicyReviewPacket:
    return PolicyReviewPacket.model_validate_json(path.read_text(encoding="utf-8"))
