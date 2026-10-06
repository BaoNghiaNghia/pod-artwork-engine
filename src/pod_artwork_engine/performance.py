from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StageCache:
    """Content-addressed cache for deterministic stage payloads."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(
        stage: str,
        version: str,
        source_hashes: list[str],
        options: dict[str, Any] | None = None,
    ) -> str:
        payload = {
            "stage": stage,
            "version": version,
            "source_hashes": source_hashes,
            "options": options or {},
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _path(
        self,
        stage: str,
        version: str,
        source_hashes: list[str],
        options: dict[str, Any] | None = None,
    ) -> Path:
        key = self._key(stage, version, source_hashes, options)
        path = self.root / stage / key[:2] / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def get(
        self,
        stage: str,
        version: str,
        source_hashes: list[str],
        options: dict[str, Any] | None = None,
    ) -> Any | None:
        path = self._path(stage, version, source_hashes, options)
        if not path.is_file():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if envelope.get("stage") != stage or envelope.get("version") != version:
            return None
        return envelope.get("payload")

    def put(
        self,
        stage: str,
        version: str,
        source_hashes: list[str],
        payload: Any,
        options: dict[str, Any] | None = None,
    ) -> Path:
        target = self._path(stage, version, source_hashes, options)
        envelope = {
            "stage": stage,
            "version": version,
            "source_hashes": source_hashes,
            "options": options or {},
            "payload": payload,
        }
        encoded = json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=target.parent,
            prefix=".cache.",
            suffix=".tmp",
        ) as handle:
            handle.write(encoded)
            temp_path = Path(handle.name)
        os.replace(temp_path, target)
        return target


class PerformanceStore:
    """Small SQLite-backed timing store used by the UI and diagnostics."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stage_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    event TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    cache_hit INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_stage_metrics_stage_created ON stage_metrics(stage, created_at)"
            )

    def record(
        self,
        *,
        job_id: str,
        stage: str,
        event: str,
        duration_ms: int,
        cache_hit: bool = False,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO stage_metrics(job_id, stage, event, duration_ms, cache_hit, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    stage,
                    event,
                    max(0, int(duration_ms)),
                    int(cache_hit),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    @staticmethod
    def _percentile(values: list[int], percentile: float) -> int:
        if not values:
            return 0
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
        return ordered[index]

    def summary(self, limit: int = 1000) -> dict[str, dict[str, int | float]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT stage, duration_ms, cache_hit
                FROM stage_metrics
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 10000)),),
            ).fetchall()

        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(str(row["stage"]), []).append(row)

        result: dict[str, dict[str, int | float]] = {}
        for stage, items in grouped.items():
            durations = [int(item["duration_ms"]) for item in items]
            cache_hits = sum(int(item["cache_hit"]) for item in items)
            result[stage] = {
                "count": len(items),
                "p50_ms": self._percentile(durations, 0.50),
                "p95_ms": self._percentile(durations, 0.95),
                "max_ms": max(durations, default=0),
                "cache_hits": cache_hits,
                "cache_hit_rate": round(cache_hits / len(items), 4) if items else 0.0,
            }
        return result
