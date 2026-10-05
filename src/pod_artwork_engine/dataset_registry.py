from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps

from .contracts import (
    DatasetMember,
    DatasetRecord,
    DatasetSplit,
    HistoricalAsset,
    HistoricalAssetRole,
    HistoricalPair,
)
from .preflight import inspect_image


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value or "historical"


def _canonical_rgba(path: Path, size: int = 96) -> Image.Image:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        alpha = image.getchannel("A")
        bbox = alpha.getbbox()
        if bbox:
            image = image.crop(bbox)

        image.thumbnail((size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        x = (size - image.width) // 2
        y = (size - image.height) // 2
        canvas.alpha_composite(image, (x, y))
        return canvas


def normalized_artwork_hash(path: Path) -> str:
    image = _canonical_rgba(path, 128)
    return hashlib.sha256(image.tobytes()).hexdigest()


def visual_hash(path: Path) -> str:
    image = _canonical_rgba(path, 64)
    white = Image.new("RGBA", image.size, (255, 255, 255, 255))
    white.alpha_composite(image)
    gray = white.convert("L")

    dh = gray.resize((9, 8), Image.Resampling.LANCZOS)
    d_bits = 0
    pixels = list(dh.getdata())
    for y in range(8):
        row = pixels[y * 9 : (y + 1) * 9]
        for x in range(8):
            d_bits = (d_bits << 1) | int(row[x] > row[x + 1])

    ah = gray.resize((8, 8), Image.Resampling.LANCZOS)
    a_pixels = list(ah.getdata())
    average = sum(a_pixels) / len(a_pixels)
    a_bits = 0
    for value in a_pixels:
        a_bits = (a_bits << 1) | int(value >= average)

    rgba = list(image.resize((16, 16), Image.Resampling.BOX).getdata())
    alpha_total = sum(pixel[3] for pixel in rgba)
    if alpha_total:
        r = round(sum(pixel[0] * pixel[3] for pixel in rgba) / alpha_total)
        g = round(sum(pixel[1] * pixel[3] for pixel in rgba) / alpha_total)
        b = round(sum(pixel[2] * pixel[3] for pixel in rgba) / alpha_total)
    else:
        r = g = b = 0
    a = round(alpha_total / len(rgba))
    return f"{d_bits:016x}:{a_bits:016x}:{r:03d},{g:03d},{b:03d},{a:03d}"


def _parse_visual_hash(value: str) -> tuple[int, int, tuple[int, int, int, int]]:
    dh, ah, rgba = value.split(":", 2)
    color = tuple(int(part) for part in rgba.split(","))
    if len(color) != 4:
        raise ValueError("invalid visual hash")
    return int(dh, 16), int(ah, 16), color  # type: ignore[return-value]


def visual_distance(left: str, right: str) -> tuple[int, int]:
    left_d, left_a, left_color = _parse_visual_hash(left)
    right_d, right_a, right_color = _parse_visual_hash(right)
    bit_distance = (left_d ^ right_d).bit_count() + (left_a ^ right_a).bit_count()
    color_distance = sum(abs(a - b) for a, b in zip(left_color, right_color, strict=True))
    return bit_distance, color_distance


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=path.parent,
        prefix=f".{path.stem}.",
        suffix=".tmp",
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


class DatasetRegistry:
    def __init__(self, database_path: Path, datasets_dir: Path) -> None:
        self.database_path = database_path
        self.datasets_dir = datasets_dir
        database_path.parent.mkdir(parents=True, exist_ok=True)
        datasets_dir.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS historical_assets (
                    asset_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    normalized_hash TEXT NOT NULL,
                    visual_hash TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    has_alpha INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(role, sha256)
                );

                CREATE TABLE IF NOT EXISTS historical_asset_locations (
                    asset_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    PRIMARY KEY(asset_id, path),
                    FOREIGN KEY(asset_id) REFERENCES historical_assets(asset_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS artwork_identities (
                    artwork_identity TEXT PRIMARY KEY,
                    canonical_target_asset_id TEXT NOT NULL,
                    normalized_hash TEXT NOT NULL,
                    visual_hash TEXT NOT NULL,
                    aspect_ratio REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(canonical_target_asset_id) REFERENCES historical_assets(asset_id)
                );
                CREATE INDEX IF NOT EXISTS idx_artwork_normalized_hash
                    ON artwork_identities(normalized_hash);

                CREATE TABLE IF NOT EXISTS historical_pairs (
                    pair_id TEXT PRIMARY KEY,
                    pair_key TEXT NOT NULL,
                    artwork_identity TEXT NOT NULL,
                    target_asset_id TEXT NOT NULL,
                    source_asset_ids_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(pair_key, target_asset_id),
                    FOREIGN KEY(artwork_identity) REFERENCES artwork_identities(artwork_identity),
                    FOREIGN KEY(target_asset_id) REFERENCES historical_assets(asset_id)
                );
                CREATE INDEX IF NOT EXISTS idx_pairs_artwork
                    ON historical_pairs(artwork_identity);

                CREATE TABLE IF NOT EXISTS artwork_split_assignments (
                    artwork_identity TEXT PRIMARY KEY,
                    split TEXT NOT NULL,
                    seed TEXT NOT NULL,
                    train_ratio REAL NOT NULL,
                    validation_ratio REAL NOT NULL,
                    golden_ratio REAL NOT NULL,
                    assigned_at TEXT NOT NULL,
                    FOREIGN KEY(artwork_identity) REFERENCES artwork_identities(artwork_identity)
                );

                CREATE TABLE IF NOT EXISTS datasets (
                    dataset_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    seed TEXT NOT NULL,
                    train_ratio REAL NOT NULL,
                    validation_ratio REAL NOT NULL,
                    golden_ratio REAL NOT NULL,
                    pair_count INTEGER NOT NULL,
                    artwork_count INTEGER NOT NULL,
                    split_counts_json TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(name, version)
                );

                CREATE TABLE IF NOT EXISTS dataset_members (
                    dataset_id TEXT NOT NULL,
                    pair_id TEXT NOT NULL,
                    artwork_identity TEXT NOT NULL,
                    split TEXT NOT NULL,
                    retrieval_eligible INTEGER NOT NULL,
                    PRIMARY KEY(dataset_id, pair_id),
                    FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id) ON DELETE CASCADE,
                    FOREIGN KEY(pair_id) REFERENCES historical_pairs(pair_id)
                );
                CREATE INDEX IF NOT EXISTS idx_dataset_members_split
                    ON dataset_members(dataset_id, split);
                """
            )

    def register_asset(self, path: Path, role: HistoricalAssetRole) -> HistoricalAsset:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(resolved)

        info = inspect_image(resolved)
        asset_id = "asset_" + hashlib.sha256(f"{role.value}:{info.sha256}".encode()).hexdigest()[:24]
        normalized = normalized_artwork_hash(resolved)
        fingerprint = visual_hash(resolved)
        created_at = utc_iso()

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM historical_assets WHERE role=? AND sha256=?",
                (role.value, info.sha256),
            ).fetchone()
            if existing:
                conn.execute(
                    "INSERT OR IGNORE INTO historical_asset_locations(asset_id, path) VALUES (?, ?)",
                    (existing["asset_id"], str(resolved)),
                )
                return self._asset_from_row(existing)

            conn.execute(
                """
                INSERT INTO historical_assets(
                    asset_id, role, path, sha256, normalized_hash, visual_hash,
                    width, height, file_size_bytes, has_alpha, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    role.value,
                    str(resolved),
                    info.sha256,
                    normalized,
                    fingerprint,
                    info.width,
                    info.height,
                    info.file_size_bytes,
                    int(info.has_alpha),
                    created_at,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO historical_asset_locations(asset_id, path) VALUES (?, ?)",
                (asset_id, str(resolved)),
            )

        return HistoricalAsset(
            asset_id=asset_id,
            role=role,
            path=str(resolved),
            sha256=info.sha256,
            normalized_hash=normalized,
            visual_hash=fingerprint,
            width=info.width,
            height=info.height,
            file_size_bytes=info.file_size_bytes,
            has_alpha=info.has_alpha,
            created_at=datetime.fromisoformat(created_at),
        )

    def resolve_artwork_identity(self, target: HistoricalAsset) -> str:
        aspect_ratio = target.width / target.height
        with self._connect() as conn:
            exact = conn.execute(
                """
                SELECT artwork_identity
                FROM artwork_identities
                WHERE normalized_hash=?
                ORDER BY artwork_identity
                LIMIT 1
                """,
                (target.normalized_hash,),
            ).fetchone()
            if exact:
                return str(exact["artwork_identity"])

            best: tuple[int, int, str] | None = None
            for row in conn.execute(
                "SELECT artwork_identity, visual_hash, aspect_ratio FROM artwork_identities"
            ).fetchall():
                candidate_ratio = float(row["aspect_ratio"])
                ratio_delta = abs(aspect_ratio - candidate_ratio) / max(aspect_ratio, candidate_ratio, 0.001)
                if ratio_delta > 0.03:
                    continue
                bits, color = visual_distance(target.visual_hash, str(row["visual_hash"]))
                if bits <= 4 and color <= 36:
                    score = (bits, color, str(row["artwork_identity"]))
                    if best is None or score < best:
                        best = score

            if best is not None:
                return best[2]

            artwork_identity = "art_" + target.normalized_hash[:24]
            conn.execute(
                """
                INSERT INTO artwork_identities(
                    artwork_identity, canonical_target_asset_id, normalized_hash,
                    visual_hash, aspect_ratio, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    artwork_identity,
                    target.asset_id,
                    target.normalized_hash,
                    target.visual_hash,
                    aspect_ratio,
                    utc_iso(),
                ),
            )
            return artwork_identity

    def register_pair(
        self,
        pair_key: str,
        source_paths: Iterable[Path],
        target_path: Path,
        metadata: dict | None = None,
    ) -> HistoricalPair:
        target = self.register_asset(target_path, HistoricalAssetRole.TARGET)
        artwork_identity = self.resolve_artwork_identity(target)

        sources_by_id: dict[str, HistoricalAsset] = {}
        for path in source_paths:
            source = self.register_asset(path, HistoricalAssetRole.SOURCE)
            sources_by_id[source.asset_id] = source
        if not sources_by_id:
            raise ValueError("historical pair requires at least one source image")

        pair_key = pair_key.strip() or Path(target.path).stem
        pair_id = "pair_" + hashlib.sha256(
            f"{pair_key.lower()}:{target.asset_id}".encode("utf-8")
        ).hexdigest()[:24]
        incoming_metadata = metadata or {}

        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT *
                FROM historical_pairs
                WHERE pair_id=? OR (pair_key=? AND target_asset_id=?)
                LIMIT 1
                """,
                (pair_id, pair_key, target.asset_id),
            ).fetchone()
            if existing:
                source_ids = set(json.loads(existing["source_asset_ids_json"]))
                source_ids.update(sources_by_id)
                combined_metadata = json.loads(existing["metadata_json"])
                combined_metadata.update(incoming_metadata)
                conn.execute(
                    """
                    UPDATE historical_pairs
                    SET source_asset_ids_json=?, metadata_json=?, artwork_identity=?
                    WHERE pair_id=?
                    """,
                    (
                        json.dumps(sorted(source_ids)),
                        json.dumps(combined_metadata, ensure_ascii=False, sort_keys=True),
                        artwork_identity,
                        existing["pair_id"],
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM historical_pairs WHERE pair_id=?",
                    (existing["pair_id"],),
                ).fetchone()
                return self._pair_from_row(row)

            created_at = utc_iso()
            source_ids = sorted(sources_by_id)
            conn.execute(
                """
                INSERT INTO historical_pairs(
                    pair_id, pair_key, artwork_identity, target_asset_id,
                    source_asset_ids_json, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pair_id,
                    pair_key,
                    artwork_identity,
                    target.asset_id,
                    json.dumps(source_ids),
                    json.dumps(incoming_metadata, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )

        return HistoricalPair(
            pair_id=pair_id,
            pair_key=pair_key,
            artwork_identity=artwork_identity,
            target_asset_id=target.asset_id,
            source_asset_ids=source_ids,
            metadata=incoming_metadata,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_pairs(self) -> list[HistoricalPair]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM historical_pairs ORDER BY pair_key, pair_id"
            ).fetchall()
        return [self._pair_from_row(row) for row in rows]

    def get_pair(self, pair_id: str) -> HistoricalPair | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM historical_pairs WHERE pair_id=?",
                (pair_id,),
            ).fetchone()
        return self._pair_from_row(row) if row else None

    def get_asset(self, asset_id: str) -> HistoricalAsset | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM historical_assets WHERE asset_id=?",
                (asset_id,),
            ).fetchone()
        return self._asset_from_row(row) if row else None

    def list_asset_locations(self, asset_id: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT path FROM historical_asset_locations WHERE asset_id=? ORDER BY path",
                (asset_id,),
            ).fetchall()
        return [str(row["path"]) for row in rows]

    def create_dataset(
        self,
        name: str,
        *,
        pair_ids: Iterable[str] | None = None,
        seed: str = "foundation-v1",
        train_ratio: float = 0.75,
        validation_ratio: float = 0.10,
        golden_ratio: float = 0.15,
    ) -> DatasetRecord:
        total = train_ratio + validation_ratio + golden_ratio
        if abs(total - 1.0) > 1e-9:
            raise ValueError("dataset split ratios must sum to 1.0")
        if min(train_ratio, validation_ratio, golden_ratio) < 0:
            raise ValueError("dataset split ratios cannot be negative")

        all_pairs = self.list_pairs()
        requested = set(pair_ids) if pair_ids is not None else None
        pairs = [pair for pair in all_pairs if requested is None or pair.pair_id in requested]
        if requested is not None:
            missing = requested - {pair.pair_id for pair in pairs}
            if missing:
                raise KeyError(f"unknown pair ids: {', '.join(sorted(missing))}")
        if not pairs:
            raise ValueError("cannot create an empty dataset")

        identities = sorted({pair.artwork_identity for pair in pairs})
        split_by_identity = self._split_identities(
            identities,
            seed=seed,
            train_ratio=train_ratio,
            validation_ratio=validation_ratio,
            golden_ratio=golden_ratio,
        )

        slug = _slug(name)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM datasets WHERE name=?",
                (name,),
            ).fetchone()
            version = int(row["version"]) + 1
        dataset_id = f"{slug}-v{version}"
        manifest_path = self.datasets_dir / dataset_id / "manifest.json"

        members = [
            DatasetMember(
                dataset_id=dataset_id,
                pair_id=pair.pair_id,
                artwork_identity=pair.artwork_identity,
                split=split_by_identity[pair.artwork_identity],
                retrieval_eligible=split_by_identity[pair.artwork_identity]
                is not DatasetSplit.GOLDEN_HOLDOUT,
            )
            for pair in pairs
        ]
        split_counts = Counter(member.split.value for member in members)
        created_at = utc_iso()

        record = DatasetRecord(
            dataset_id=dataset_id,
            name=name,
            version=version,
            seed=seed,
            train_ratio=train_ratio,
            validation_ratio=validation_ratio,
            golden_ratio=golden_ratio,
            pair_count=len(pairs),
            artwork_count=len(identities),
            split_counts=dict(split_counts),
            manifest_path=str(manifest_path),
            created_at=datetime.fromisoformat(created_at),
        )

        manifest = {
            "schema_version": "1.0",
            "dataset": record.model_dump(mode="json"),
            "members": [],
        }
        for pair, member in zip(pairs, members, strict=True):
            target = self.get_asset(pair.target_asset_id)
            sources = [self.get_asset(asset_id) for asset_id in pair.source_asset_ids]
            manifest["members"].append(
                {
                    "pair": pair.model_dump(mode="json"),
                    "split": member.split.value,
                    "retrieval_eligible": member.retrieval_eligible,
                    "target": target.model_dump(mode="json") if target else None,
                    "sources": [
                        source.model_dump(mode="json") for source in sources if source is not None
                    ],
                }
            )

        _atomic_json(manifest_path, manifest)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO datasets(
                    dataset_id, name, version, seed, train_ratio, validation_ratio,
                    golden_ratio, pair_count, artwork_count, split_counts_json,
                    manifest_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_id,
                    name,
                    version,
                    seed,
                    train_ratio,
                    validation_ratio,
                    golden_ratio,
                    len(pairs),
                    len(identities),
                    json.dumps(dict(split_counts), sort_keys=True),
                    str(manifest_path),
                    created_at,
                ),
            )
            conn.executemany(
                """
                INSERT INTO dataset_members(
                    dataset_id, pair_id, artwork_identity, split, retrieval_eligible
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        member.dataset_id,
                        member.pair_id,
                        member.artwork_identity,
                        member.split.value,
                        int(member.retrieval_eligible),
                    )
                    for member in members
                ],
            )
        return record

    def list_datasets(self) -> list[DatasetRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM datasets ORDER BY created_at DESC, name, version DESC"
            ).fetchall()
        return [self._dataset_from_row(row) for row in rows]

    def get_dataset(self, dataset_id: str) -> DatasetRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM datasets WHERE dataset_id=?",
                (dataset_id,),
            ).fetchone()
        return self._dataset_from_row(row) if row else None

    def list_members(
        self,
        dataset_id: str,
        *,
        split: DatasetSplit | None = None,
        retrieval_only: bool = False,
    ) -> list[DatasetMember]:
        query = "SELECT * FROM dataset_members WHERE dataset_id=?"
        args: list[object] = [dataset_id]
        if split is not None:
            query += " AND split=?"
            args.append(split.value)
        if retrieval_only:
            query += " AND retrieval_eligible=1"
        query += " ORDER BY artwork_identity, pair_id"

        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [
            DatasetMember(
                dataset_id=str(row["dataset_id"]),
                pair_id=str(row["pair_id"]),
                artwork_identity=str(row["artwork_identity"]),
                split=DatasetSplit(str(row["split"])),
                retrieval_eligible=bool(row["retrieval_eligible"]),
            )
            for row in rows
        ]

    def _split_identities(
        self,
        identities: list[str],
        *,
        seed: str,
        train_ratio: float,
        validation_ratio: float,
        golden_ratio: float,
    ) -> dict[str, DatasetSplit]:
        # Split assignment is persistent per artwork identity. Once an artwork
        # enters Golden Holdout it can never silently move into train in a later
        # dataset version as the corpus grows.
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT artwork_identity, split FROM artwork_split_assignments"
            ).fetchall()
            result = {
                str(row["artwork_identity"]): DatasetSplit(str(row["split"]))
                for row in rows
                if str(row["artwork_identity"]) in identities
            }

            missing = [identity for identity in identities if identity not in result]
            ranked_missing = sorted(
                missing,
                key=lambda identity: hashlib.sha256(
                    f"{seed}:{identity}".encode("utf-8")
                ).hexdigest(),
            )

            for identity in ranked_missing:
                digest = hashlib.sha256(f"{seed}:{identity}".encode("utf-8")).digest()
                bucket = int.from_bytes(digest[:8], "big") / float(2**64)
                if bucket < train_ratio:
                    split = DatasetSplit.TRAIN
                elif bucket < train_ratio + validation_ratio:
                    split = DatasetSplit.VALIDATION
                else:
                    split = DatasetSplit.GOLDEN_HOLDOUT
                result[identity] = split

            if len(identities) >= 3 and ranked_missing:
                if golden_ratio > 0 and DatasetSplit.GOLDEN_HOLDOUT not in result.values():
                    candidate = ranked_missing[-1]
                    result[candidate] = DatasetSplit.GOLDEN_HOLDOUT
                if validation_ratio > 0 and DatasetSplit.VALIDATION not in result.values():
                    candidates = [
                        identity
                        for identity in reversed(ranked_missing)
                        if result[identity] is not DatasetSplit.GOLDEN_HOLDOUT
                    ]
                    if candidates:
                        result[candidates[0]] = DatasetSplit.VALIDATION

            now = utc_iso()
            for identity in ranked_missing:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO artwork_split_assignments(
                        artwork_identity, split, seed, train_ratio,
                        validation_ratio, golden_ratio, assigned_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identity,
                        result[identity].value,
                        seed,
                        train_ratio,
                        validation_ratio,
                        golden_ratio,
                        now,
                    ),
                )

        return {identity: result[identity] for identity in identities}

    @staticmethod
    def _asset_from_row(row: sqlite3.Row) -> HistoricalAsset:
        return HistoricalAsset(
            asset_id=str(row["asset_id"]),
            role=HistoricalAssetRole(str(row["role"])),
            path=str(row["path"]),
            sha256=str(row["sha256"]),
            normalized_hash=str(row["normalized_hash"]),
            visual_hash=str(row["visual_hash"]),
            width=int(row["width"]),
            height=int(row["height"]),
            file_size_bytes=int(row["file_size_bytes"]),
            has_alpha=bool(row["has_alpha"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _pair_from_row(row: sqlite3.Row) -> HistoricalPair:
        return HistoricalPair(
            pair_id=str(row["pair_id"]),
            pair_key=str(row["pair_key"]),
            artwork_identity=str(row["artwork_identity"]),
            target_asset_id=str(row["target_asset_id"]),
            source_asset_ids=list(json.loads(row["source_asset_ids_json"])),
            metadata=dict(json.loads(row["metadata_json"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _dataset_from_row(row: sqlite3.Row) -> DatasetRecord:
        return DatasetRecord(
            dataset_id=str(row["dataset_id"]),
            name=str(row["name"]),
            version=int(row["version"]),
            seed=str(row["seed"]),
            train_ratio=float(row["train_ratio"]),
            validation_ratio=float(row["validation_ratio"]),
            golden_ratio=float(row["golden_ratio"]),
            pair_count=int(row["pair_count"]),
            artwork_count=int(row["artwork_count"]),
            split_counts=dict(json.loads(row["split_counts_json"])),
            manifest_path=str(row["manifest_path"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )
