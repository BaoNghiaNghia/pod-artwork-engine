from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import Field

from .contracts import DatasetSplit, StrictModel
from .dataset_registry import normalized_artwork_hash, visual_distance, visual_hash
from .historical_import import (
    _pick_canonical_target,
    _strict_visual_source_match,
    discover_images,
    pairing_key,
)
from .preflight import inspect_image


class HistoricalOnboardingMode(StrEnum):
    FOLDERS = "folders"
    MANIFEST = "manifest"


class HistoricalOnboardingStatus(StrEnum):
    READY_TO_IMPORT = "ready_to_import"
    BLOCKED = "blocked"


class HistoricalPairPreview(StrictModel):
    pair_key: str
    source_paths: list[str] = Field(min_length=1)
    target_path: str
    pairing_method: str
    target_duplicate_paths: list[str] = Field(default_factory=list)


class HistoricalOnboardingReport(StrictModel):
    mode: HistoricalOnboardingMode
    status: HistoricalOnboardingStatus
    ready_to_import: bool = False
    source_root: str | None = None
    target_root: str | None = None
    manifest_path: str | None = None
    seed: str = "foundation-v1"
    allow_visual_fallback: bool = True
    source_count: int = 0
    target_count: int = 0
    valid_source_count: int = 0
    valid_target_count: int = 0
    invalid_images: list[str] = Field(default_factory=list)
    pair_count: int = 0
    pairs: list[HistoricalPairPreview] = Field(default_factory=list)
    unmatched_sources: list[str] = Field(default_factory=list)
    unmatched_targets: list[str] = Field(default_factory=list)
    target_duplicates: list[str] = Field(default_factory=list)
    exact_duplicate_sources: list[list[str]] = Field(default_factory=list)
    exact_duplicate_targets: list[list[str]] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    projected_pair_split_counts: dict[str, int] = Field(default_factory=dict)
    projected_artwork_split_counts: dict[str, int] = Field(default_factory=dict)
    projected_golden_case_count: int = 0
    minimum_golden_cases: int = 3
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    mutates_registry: bool = False
    production_execution_enabled: bool = False
    created_at: datetime


def _safe_images(paths: list[Path]) -> tuple[list[Path], list[str], dict[Path, str]]:
    valid: list[Path] = []
    invalid: list[str] = []
    sha_by_path: dict[Path, str] = {}
    for path in paths:
        try:
            info = inspect_image(path)
        except Exception as exc:
            invalid.append(f"{path}: {type(exc).__name__}: {exc}")
            continue
        valid.append(path)
        sha_by_path[path] = info.sha256
    return valid, invalid, sha_by_path


def _duplicate_groups(
    paths: list[Path],
    sha_by_path: dict[Path, str],
) -> list[list[str]]:
    by_sha: dict[str, list[Path]] = defaultdict(list)
    for path in paths:
        sha = sha_by_path.get(path)
        if sha:
            by_sha[sha].append(path)
    return [
        [str(path) for path in sorted(group)]
        for group in by_sha.values()
        if len(group) > 1
    ]


def _project_splits(
    pairs: list[HistoricalPairPreview],
    *,
    seed: str,
    target_identity_by_path: dict[str, str],
    train_ratio: float = 0.75,
    validation_ratio: float = 0.10,
    golden_ratio: float = 0.15,
) -> tuple[dict[str, int], dict[str, int]]:
    identities = sorted(
        {
            target_identity_by_path[pair.target_path]
            for pair in pairs
            if pair.target_path in target_identity_by_path
        }
    )
    split_by_identity: dict[str, DatasetSplit] = {}
    ranked = sorted(
        identities,
        key=lambda identity: hashlib.sha256(
            f"{seed}:{identity}".encode("utf-8")
        ).hexdigest(),
    )
    for identity in ranked:
        digest = hashlib.sha256(f"{seed}:{identity}".encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:8], "big") / float(2**64)
        if bucket < train_ratio:
            split = DatasetSplit.TRAIN
        elif bucket < train_ratio + validation_ratio:
            split = DatasetSplit.VALIDATION
        else:
            split = DatasetSplit.GOLDEN_HOLDOUT
        split_by_identity[identity] = split

    if len(identities) >= 3 and ranked:
        if golden_ratio > 0 and DatasetSplit.GOLDEN_HOLDOUT not in split_by_identity.values():
            split_by_identity[ranked[-1]] = DatasetSplit.GOLDEN_HOLDOUT
        if validation_ratio > 0 and DatasetSplit.VALIDATION not in split_by_identity.values():
            candidates = [
                identity
                for identity in reversed(ranked)
                if split_by_identity[identity] is not DatasetSplit.GOLDEN_HOLDOUT
            ]
            if candidates:
                split_by_identity[candidates[0]] = DatasetSplit.VALIDATION

    artwork_counts = Counter(split.value for split in split_by_identity.values())
    pair_counts: Counter[str] = Counter()
    for pair in pairs:
        identity = target_identity_by_path.get(pair.target_path)
        if identity is None:
            continue
        pair_counts[split_by_identity[identity].value] += 1
    return dict(pair_counts), dict(artwork_counts)


def _project_target_identities(
    pairs: list[HistoricalPairPreview],
) -> dict[str, str]:
    identities: dict[str, tuple[str, str, float]] = {}
    result: dict[str, str] = {}

    for pair in pairs:
        path = Path(pair.target_path)
        normalized = normalized_artwork_hash(path)
        fingerprint = visual_hash(path)
        with Image.open(path) as image:
            aspect_ratio = image.width / max(image.height, 1)

        exact = next(
            (
                identity
                for identity, (known_normalized, _, _) in identities.items()
                if known_normalized == normalized
            ),
            None,
        )
        if exact is not None:
            result[pair.target_path] = exact
            continue

        best: tuple[int, int, str] | None = None
        for identity, (_, known_visual, known_ratio) in identities.items():
            ratio_delta = abs(aspect_ratio - known_ratio) / max(
                aspect_ratio,
                known_ratio,
                0.001,
            )
            if ratio_delta > 0.03:
                continue
            bits, color = visual_distance(fingerprint, known_visual)
            if bits <= 4 and color <= 36:
                candidate = (bits, color, identity)
                if best is None or candidate < best:
                    best = candidate

        if best is not None:
            result[pair.target_path] = best[2]
            continue

        identity = "art_" + normalized[:24]
        identities[identity] = (normalized, fingerprint, aspect_ratio)
        result[pair.target_path] = identity

    return result


def _finalize(
    *,
    mode: HistoricalOnboardingMode,
    pairs: list[HistoricalPairPreview],
    source_count: int,
    target_count: int,
    valid_source_count: int,
    valid_target_count: int,
    invalid_images: list[str],
    unmatched_sources: list[str],
    unmatched_targets: list[str],
    target_duplicates: list[str],
    exact_duplicate_sources: list[list[str]],
    exact_duplicate_targets: list[list[str]],
    conflicts: list[str],
    target_identity_by_path: dict[str, str] | None,
    seed: str,
    minimum_golden_cases: int,
    allow_visual_fallback: bool,
    source_root: Path | None = None,
    target_root: Path | None = None,
    manifest_path: Path | None = None,
) -> HistoricalOnboardingReport:
    if target_identity_by_path is None:
        target_identity_by_path = _project_target_identities(pairs)
    pair_splits, artwork_splits = _project_splits(
        pairs,
        seed=seed,
        target_identity_by_path=target_identity_by_path,
    )
    golden_count = pair_splits.get(DatasetSplit.GOLDEN_HOLDOUT.value, 0)

    blockers: list[str] = []
    warnings: list[str] = []

    if source_count == 0:
        blockers.append("historical_onboarding_no_source_images")
    if target_count == 0:
        blockers.append("historical_onboarding_no_target_images")
    if invalid_images:
        blockers.append("historical_onboarding_invalid_images")
    if conflicts:
        blockers.append("historical_onboarding_pair_conflicts")
    if unmatched_sources:
        blockers.append("historical_onboarding_unmatched_sources")
    if unmatched_targets:
        blockers.append("historical_onboarding_unmatched_targets")
    if not pairs:
        blockers.append("historical_onboarding_no_importable_pairs")
    if golden_count < minimum_golden_cases:
        blockers.append("historical_onboarding_insufficient_projected_golden_cases")

    if target_duplicates:
        warnings.append("historical_onboarding_equivalent_target_duplicates")
    if exact_duplicate_sources:
        warnings.append("historical_onboarding_exact_duplicate_sources")
    if exact_duplicate_targets:
        warnings.append("historical_onboarding_exact_duplicate_targets")
    if any(pair.pairing_method == "visual_fallback" for pair in pairs):
        warnings.append("historical_onboarding_visual_fallback_used")
    warnings.append("historical_onboarding_projected_split_not_persisted")

    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))
    ready = not blockers

    return HistoricalOnboardingReport(
        mode=mode,
        status=(
            HistoricalOnboardingStatus.READY_TO_IMPORT
            if ready
            else HistoricalOnboardingStatus.BLOCKED
        ),
        ready_to_import=ready,
        source_root=str(source_root) if source_root is not None else None,
        target_root=str(target_root) if target_root is not None else None,
        manifest_path=str(manifest_path) if manifest_path is not None else None,
        seed=seed,
        allow_visual_fallback=allow_visual_fallback,
        source_count=source_count,
        target_count=target_count,
        valid_source_count=valid_source_count,
        valid_target_count=valid_target_count,
        invalid_images=invalid_images,
        pair_count=len(pairs),
        pairs=pairs,
        unmatched_sources=unmatched_sources,
        unmatched_targets=unmatched_targets,
        target_duplicates=target_duplicates,
        exact_duplicate_sources=exact_duplicate_sources,
        exact_duplicate_targets=exact_duplicate_targets,
        conflicts=conflicts,
        projected_pair_split_counts=pair_splits,
        projected_artwork_split_counts=artwork_splits,
        projected_golden_case_count=golden_count,
        minimum_golden_cases=minimum_golden_cases,
        blockers=blockers,
        warnings=warnings,
        mutates_registry=False,
        production_execution_enabled=False,
        created_at=datetime.now(timezone.utc),
    )


class HistoricalOnboardingBuilder:
    """Read-only validation and pair preview before HistoricalImporter mutates registry."""

    def build_folders(
        self,
        source_dir: Path,
        target_dir: Path,
        *,
        id_regex: str | None = None,
        seed: str = "foundation-v1",
        allow_visual_fallback: bool = True,
        minimum_golden_cases: int = 3,
    ) -> HistoricalOnboardingReport:
        minimum_golden_cases = max(1, minimum_golden_cases)
        source_root = source_dir.expanduser().resolve()
        target_root = target_dir.expanduser().resolve()
        if not source_root.is_dir() or not target_root.is_dir():
            conflicts: list[str] = []
            if not source_root.is_dir():
                conflicts.append(f"source directory not found: {source_root}")
            if not target_root.is_dir():
                conflicts.append(f"target directory not found: {target_root}")
            return _finalize(
                mode=HistoricalOnboardingMode.FOLDERS,
                pairs=[],
                source_count=0,
                target_count=0,
                valid_source_count=0,
                valid_target_count=0,
                invalid_images=[],
                unmatched_sources=[],
                unmatched_targets=[],
                target_duplicates=[],
                exact_duplicate_sources=[],
                exact_duplicate_targets=[],
                conflicts=conflicts,
                target_identity_by_path={},
                seed=seed,
                minimum_golden_cases=minimum_golden_cases,
                allow_visual_fallback=allow_visual_fallback,
                source_root=source_root,
                target_root=target_root,
            )

        all_sources = discover_images(source_root)
        all_targets = discover_images(target_root)
        sources, invalid_sources, source_sha = _safe_images(all_sources)
        targets, invalid_targets, target_sha = _safe_images(all_targets)
        invalid_images = [*invalid_sources, *invalid_targets]

        source_groups: dict[str, list[Path]] = defaultdict(list)
        target_groups: dict[str, list[Path]] = defaultdict(list)
        for source in sources:
            source_groups[pairing_key(source, id_regex)].append(source)
        for target in targets:
            target_groups[pairing_key(target, id_regex)].append(target)

        used_sources: set[Path] = set()
        used_targets: set[Path] = set()
        pairs: list[HistoricalPairPreview] = []
        target_duplicates: list[str] = []
        conflicts: list[str] = []
        for key in sorted(target_groups):
            canonical, duplicates, conflict = _pick_canonical_target(target_groups[key])
            if conflict:
                conflicts.append(conflict)
                continue
            if canonical is None:
                continue

            target_duplicates.extend(str(path) for path in duplicates)
            matched_sources = list(source_groups.get(key, []))
            method = "pairing_key"

            if not matched_sources and allow_visual_fallback:
                available_sources = [
                    path for path in sources if path not in used_sources
                ]
                visual_matches = [
                    source
                    for source in available_sources
                    if _strict_visual_source_match(source, [canonical]) is not None
                ]
                if len(visual_matches) == 1:
                    matched_sources = visual_matches
                    method = "visual_fallback"
                elif len(visual_matches) > 1:
                    conflicts.append(
                        "multiple visual fallback sources match target: "
                        + str(canonical)
                    )
                    continue

            if not matched_sources:
                continue

            preview = HistoricalPairPreview(
                pair_key=key,
                source_paths=[str(path) for path in sorted(matched_sources)],
                target_path=str(canonical),
                pairing_method=method,
                target_duplicate_paths=[str(path) for path in sorted(duplicates)],
            )
            pairs.append(preview)
            used_sources.update(matched_sources)
            used_targets.add(canonical)
            used_targets.update(duplicates)

        unmatched_sources = [
            str(path) for path in sources if path not in used_sources
        ]
        unmatched_targets = [
            str(path) for path in targets if path not in used_targets
        ]

        return _finalize(
            mode=HistoricalOnboardingMode.FOLDERS,
            pairs=pairs,
            source_count=len(all_sources),
            target_count=len(all_targets),
            valid_source_count=len(sources),
            valid_target_count=len(targets),
            invalid_images=invalid_images,
            unmatched_sources=unmatched_sources,
            unmatched_targets=unmatched_targets,
            target_duplicates=sorted(set(target_duplicates)),
            exact_duplicate_sources=_duplicate_groups(sources, source_sha),
            exact_duplicate_targets=_duplicate_groups(targets, target_sha),
            conflicts=conflicts,
            target_identity_by_path=None,
            seed=seed,
            minimum_golden_cases=minimum_golden_cases,
            allow_visual_fallback=allow_visual_fallback,
            source_root=source_root,
            target_root=target_root,
        )

    def build_manifest(
        self,
        manifest_path: Path,
        *,
        seed: str = "foundation-v1",
        minimum_golden_cases: int = 3,
    ) -> HistoricalOnboardingReport:
        minimum_golden_cases = max(1, minimum_golden_cases)
        resolved = manifest_path.expanduser().resolve()
        conflicts: list[str] = []
        if not resolved.is_file():
            conflicts.append(f"manifest not found: {resolved}")
            return _finalize(
                mode=HistoricalOnboardingMode.MANIFEST,
                pairs=[],
                source_count=0,
                target_count=0,
                valid_source_count=0,
                valid_target_count=0,
                invalid_images=[],
                unmatched_sources=[],
                unmatched_targets=[],
                target_duplicates=[],
                exact_duplicate_sources=[],
                exact_duplicate_targets=[],
                conflicts=conflicts,
                target_identity_by_path={},
                seed=seed,
                minimum_golden_cases=minimum_golden_cases,
                allow_visual_fallback=False,
                manifest_path=resolved,
            )

        try:
            payload: Any = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            conflicts.append(f"invalid manifest JSON: {exc}")
            payload = None
        records = payload.get("pairs") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            conflicts.append("historical manifest must contain a list or {pairs: [...]} object")
            records = []

        base = resolved.parent
        source_paths: list[Path] = []
        target_paths: list[Path] = []
        raw_pairs: list[tuple[str, list[Path], Path]] = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                conflicts.append(f"manifest record {index} is not an object")
                continue
            target_value = record.get("target")
            sources_value = record.get("sources")
            if not isinstance(target_value, str) or not isinstance(sources_value, list):
                conflicts.append(
                    f"manifest record {index} requires target:string and sources:list"
                )
                continue

            target = self._resolve_manifest_path(base, target_value)
            sources = [
                self._resolve_manifest_path(base, value)
                for value in sources_value
                if isinstance(value, str)
            ]
            if not sources:
                conflicts.append(f"manifest record {index} has no source images")
                continue
            key = str(
                record.get("pair_key")
                or record.get("design_id")
                or pairing_key(target)
            ).strip()
            if not key:
                conflicts.append(f"manifest record {index} has an empty pair key")
                continue
            target_paths.append(target)
            source_paths.extend(sources)
            raw_pairs.append((key, sources, target))

        unique_sources = sorted(set(source_paths))
        unique_targets = sorted(set(target_paths))
        valid_sources, invalid_sources, source_sha = _safe_images(unique_sources)
        valid_targets, invalid_targets, target_sha = _safe_images(unique_targets)
        valid_source_set = set(valid_sources)
        valid_target_set = set(valid_targets)

        pairs: list[HistoricalPairPreview] = []
        keys_seen: dict[str, str] = {}
        for key, sources, target in raw_pairs:
            if target not in valid_target_set or any(
                source not in valid_source_set for source in sources
            ):
                continue
            normalized = normalized_artwork_hash(target)
            prior = keys_seen.get(key)
            if prior is not None and prior != normalized:
                conflicts.append(
                    f"multiple non-equivalent targets share manifest pair key: {key}"
                )
                continue
            keys_seen[key] = normalized
            preview = HistoricalPairPreview(
                pair_key=key,
                source_paths=[str(path) for path in sources],
                target_path=str(target),
                pairing_method="manifest",
            )
            pairs.append(preview)

        referenced_sources = {
            source
            for _, sources, _ in raw_pairs
            for source in sources
        }
        referenced_targets = {target for _, _, target in raw_pairs}
        unmatched_sources = [
            str(path)
            for path in referenced_sources
            if path not in valid_source_set
        ]
        unmatched_targets = [
            str(path)
            for path in referenced_targets
            if path not in valid_target_set
        ]

        return _finalize(
            mode=HistoricalOnboardingMode.MANIFEST,
            pairs=pairs,
            source_count=len(unique_sources),
            target_count=len(unique_targets),
            valid_source_count=len(valid_sources),
            valid_target_count=len(valid_targets),
            invalid_images=[*invalid_sources, *invalid_targets],
            unmatched_sources=sorted(unmatched_sources),
            unmatched_targets=sorted(unmatched_targets),
            target_duplicates=[],
            exact_duplicate_sources=_duplicate_groups(valid_sources, source_sha),
            exact_duplicate_targets=_duplicate_groups(valid_targets, target_sha),
            conflicts=conflicts,
            target_identity_by_path=None,
            seed=seed,
            minimum_golden_cases=minimum_golden_cases,
            allow_visual_fallback=False,
            manifest_path=resolved,
        )

    @staticmethod
    def _resolve_manifest_path(base: Path, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base / path
        return path.resolve()
