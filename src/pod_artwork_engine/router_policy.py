from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from .contracts import SCHEMA_VERSION, StrictModel


class RouterPolicy(StrictModel):
    schema_version: str = SCHEMA_VERSION
    policy_id: str = "phase1-router-default"
    version: str = "1"
    deterministic_text_logo_min_confidence: float = Field(default=0.50, ge=0, le=1)
    clean_passthrough_min_confidence: float = Field(default=0.65, ge=0, le=1)
    low_confidence_remote_below: float = Field(default=0.50, ge=0, le=1)
    quick_2d_remote_first: bool = True
    remote_complex_enabled: bool = True


DEFAULT_ROUTER_POLICY = RouterPolicy()


def load_router_policy(path: Path | None) -> RouterPolicy:
    if path is None:
        return DEFAULT_ROUTER_POLICY.model_copy(deep=True)
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Router policy not found: {resolved}")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    return RouterPolicy.model_validate(payload)
