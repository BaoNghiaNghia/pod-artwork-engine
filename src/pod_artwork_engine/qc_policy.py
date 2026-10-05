from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from .contracts import SCHEMA_VERSION, QualityMode, StrictModel


class QCModePolicy(StrictModel):
    semantic_min_score: float = Field(ge=0, le=1)
    object_fidelity_min: float = Field(ge=0, le=1)
    required_native_long_edge: int = Field(gt=0)
    min_resolution_score: float = Field(ge=0, le=1)


class QCPolicy(StrictModel):
    schema_version: str = SCHEMA_VERSION
    policy_id: str = "phase1-default"
    version: str = "1"
    quick_2d: QCModePolicy = Field(
        default_factory=lambda: QCModePolicy(
            semantic_min_score=0.20,
            object_fidelity_min=0.0,
            required_native_long_edge=300,
            min_resolution_score=0.0,
        )
    )
    print_ready: QCModePolicy = Field(
        default_factory=lambda: QCModePolicy(
            semantic_min_score=0.35,
            object_fidelity_min=0.60,
            required_native_long_edge=700,
            min_resolution_score=0.55,
        )
    )
    max_fidelity: QCModePolicy = Field(
        default_factory=lambda: QCModePolicy(
            semantic_min_score=0.50,
            object_fidelity_min=0.72,
            required_native_long_edge=1000,
            min_resolution_score=0.75,
        )
    )

    def for_mode(self, mode: QualityMode) -> QCModePolicy:
        if mode is QualityMode.QUICK_2D:
            return self.quick_2d
        if mode is QualityMode.PRINT_READY:
            return self.print_ready
        return self.max_fidelity


DEFAULT_QC_POLICY = QCPolicy()


def load_qc_policy(path: Path | None) -> QCPolicy:
    if path is None:
        return DEFAULT_QC_POLICY.model_copy(deep=True)
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"QC policy not found: {resolved}")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    return QCPolicy.model_validate(payload)
