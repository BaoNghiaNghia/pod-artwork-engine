from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class CheckpointManager:
    def __init__(self, jobs_root: Path) -> None:
        self.jobs_root = jobs_root

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_root / job_id

    def checkpoint_dir(self, job_id: str) -> Path:
        path = self.job_dir(job_id) / "checkpoints"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def path(self, job_id: str, stage: str) -> Path:
        return self.checkpoint_dir(job_id) / f"{stage}.json"

    def exists(self, job_id: str, stage: str) -> bool:
        return self.path(job_id, stage).exists()

    def read(self, job_id: str, stage: str) -> dict[str, Any] | list[Any] | None:
        path = self.path(job_id, stage)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, job_id: str, stage: str, payload: Any) -> Path:
        target = self.path(job_id, stage)
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()

        envelope = {
            "stage": stage,
            "payload_sha256": digest,
            "payload": payload,
        }
        content = json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True)

        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=target.parent,
            prefix=f".{stage}.",
            suffix=".tmp",
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)

        os.replace(temp_path, target)
        return target

    def payload(self, job_id: str, stage: str) -> Any | None:
        envelope = self.read(job_id, stage)
        if not isinstance(envelope, dict):
            return None
        return envelope.get("payload")
