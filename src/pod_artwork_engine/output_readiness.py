from __future__ import annotations

from .contracts import DesignSpec, MaterialSeparationEvidence
from .reconstruction import CandidateInfo


def artwork_output_blockers(
    design_spec: DesignSpec,
    material: MaterialSeparationEvidence,
    candidate: CandidateInfo,
    *,
    used_remote: bool,
) -> list[str]:
    """Prevent a mockup crop from masquerading as a reconstructed 2D print.

    This is an explicit, narrow fail-closed gate. When evidence says separating
    garment material is unresolved, a local crop/soft alpha mask is not a
    recovered design. Image resolution/technical PNG QC cannot validate this.
    Already transparent artwork, verified background removal and genuinely
    reconstructed or deterministic redraw candidates remain on the normal QC
    path.
    """
    if (
        candidate.local_baseline
        and not used_remote
        and material.fail_closed
        and "need_semantic_reconstruction" in design_spec.required_capabilities
    ):
        return [
            "artwork_not_isolated_from_product_mockup",
            "semantic_provider_not_used",
        ]
    return []
