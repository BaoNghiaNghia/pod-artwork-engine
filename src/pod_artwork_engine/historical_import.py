from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from .contracts import DatasetRecord
from .dataset_registry import DatasetRegistry, normalized_artwork_hash, visual_distance, visual_hash


IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".tif",
    ".tiff",
    ".bmp",
}

ROLE_TOKENS = {
    "source",
    "src",
    "input",
    "reference",
    "references",
    "ref",
    "mockup",
    "product",
    "front",
    "back",
    "left",
    "right",
    "side",
    "angle",
    "angled",
    "detail",
    "final",
    "target",
    "master",
    "artwork",
    "output",
    "approved",
    "clean",
    "transparent",
    "2d",
}


@dataclass
class HistoricalImportReport:
    source_count: int = 0
    target_count: int = 0
    imported_pair_ids: list[str] = field(default_factory=list)
    unmatched_sources: list[str] = field(default_factory=list)
    unmatched_targets: list[str] = field(default_factory=list)
    target_duplicates: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dataset: DatasetRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["dataset"] = (
            self.dataset.model_dump(mode="json") if self.dataset is not None else None
        )
        return payload


def discover_images(root: Path) -> list[Path]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def pairing_key(path: Path, id_regex: str | None = None) -> str:
    candidate = path.stem
    if id_regex:
        match = re.search(id_regex, str(path))
        if match:
            if "id" in match.groupdict():
                candidate = match.group("id")
            elif match.groups():
                candidate = match.group(1)
            else:
                candidate = match.group(0)

    normalized = unicodedata.normalize("NFKD", candidate)
    normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]

    while tokens and tokens[0] in ROLE_TOKENS:
        tokens.pop(0)
    while tokens and tokens[-1] in ROLE_TOKENS:
        tokens.pop()

    if len(tokens) > 1 and tokens[-1].isdigit() and len(tokens[-1]) <= 2:
        # Common export suffixes such as design-front-1.png. Numeric IDs with
        # meaningful prefixes remain available through --id-regex.
        tokens.pop()
        while tokens and tokens[-1] in ROLE_TOKENS:
            tokens.pop()

    return "-".join(tokens) or normalized or path.stem.lower()


def _image_aspect(path: Path) -> float:
    with Image.open(path) as image:
        return image.width / max(image.height, 1)


def _pick_canonical_target(paths: list[Path]) -> tuple[Path | None, list[Path], str | None]:
    if len(paths) == 1:
        return paths[0], [], None

    by_normalized: dict[str, list[Path]] = {}
    for path in paths:
        by_normalized.setdefault(normalized_artwork_hash(path), []).append(path)

    if len(by_normalized) != 1:
        names = ", ".join(str(path) for path in paths)
        return None, [], f"multiple non-equivalent targets share the same pairing key: {names}"

    ranked = sorted(
        paths,
        key=lambda path: (
            -(path.stat().st_size),
            str(path).lower(),
        ),
    )
    return ranked[0], ranked[1:], None


def _strict_visual_source_match(source: Path, targets: list[Path]) -> Path | None:
    source_hash = visual_hash(source)
    source_ratio = _image_aspect(source)
    scored: list[tuple[int, int, str, Path]] = []
    for target in targets:
        target_ratio = _image_aspect(target)
        ratio_delta = abs(source_ratio - target_ratio) / max(source_ratio, target_ratio, 0.001)
        if ratio_delta > 0.03:
            continue
        bits, color = visual_distance(source_hash, visual_hash(target))
        if bits <= 6 and color <= 45:
            scored.append((bits, color, str(target).lower(), target))

    if not scored:
        return None
    scored.sort()
    if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
        return None
    return scored[0][3]


class HistoricalImporter:
    def __init__(self, registry: DatasetRegistry) -> None:
        self.registry = registry

    def import_folders(
        self,
        source_dir: Path,
        target_dir: Path,
        *,
        dataset_name: str = "historical",
        id_regex: str | None = None,
        seed: str = "foundation-v1",
        allow_visual_fallback: bool = True,
    ) -> HistoricalImportReport:
        sources = discover_images(source_dir)
        targets = discover_images(target_dir)
        report = HistoricalImportReport(
            source_count=len(sources),
            target_count=len(targets),
        )

        source_groups: dict[str, list[Path]] = {}
        target_groups: dict[str, list[Path]] = {}
        for source in sources:
            source_groups.setdefault(pairing_key(source, id_regex), []).append(source)
        for target in targets:
            target_groups.setdefault(pairing_key(target, id_regex), []).append(target)

        used_source_paths: set[Path] = set()
        used_target_paths: set[Path] = set()

        for key in sorted(target_groups):
            canonical, duplicates, conflict = _pick_canonical_target(target_groups[key])
            if conflict:
                report.conflicts.append(conflict)
                continue
            if canonical is None:
                continue
            report.target_duplicates.extend(str(path) for path in duplicates)

            matched_sources = list(source_groups.get(key, []))
            if not matched_sources and allow_visual_fallback:
                unmatched_candidates = [
                    source for source in sources if source not in used_source_paths
                ]
                visual_matches = [
                    source
                    for source in unmatched_candidates
                    if _strict_visual_source_match(source, [canonical]) is not None
                ]
                if len(visual_matches) == 1:
                    matched_sources = visual_matches
                    report.warnings.append(
                        f"paired by strict visual fallback: {visual_matches[0]} -> {canonical}"
                    )

            if not matched_sources:
                report.unmatched_targets.append(str(canonical))
                continue

            pair = self.registry.register_pair(
                key,
                matched_sources,
                canonical,
                metadata={
                    "import_mode": "folder",
                    "pairing_key": key,
                    "source_root": str(source_dir.expanduser().resolve()),
                    "target_root": str(target_dir.expanduser().resolve()),
                },
            )
            report.imported_pair_ids.append(pair.pair_id)
            used_source_paths.update(matched_sources)
            used_target_paths.add(canonical)
            used_target_paths.update(duplicates)

        report.unmatched_sources = [
            str(path) for path in sources if path not in used_source_paths
        ]
        for target in targets:
            if target not in used_target_paths and str(target) not in report.unmatched_targets:
                key = pairing_key(target, id_regex)
                if not any(str(target) in conflict for conflict in report.conflicts):
                    report.unmatched_targets.append(str(target))

        report.imported_pair_ids = sorted(set(report.imported_pair_ids))
        if report.imported_pair_ids:
            report.dataset = self.registry.create_dataset(
                dataset_name,
                seed=seed,
            )
        return report

    def import_manifest(
        self,
        manifest_path: Path,
        *,
        dataset_name: str = "historical",
        seed: str = "foundation-v1",
    ) -> HistoricalImportReport:
        manifest_path = manifest_path.expanduser().resolve()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            records = payload.get("pairs")
        else:
            records = payload
        if not isinstance(records, list):
            raise ValueError("historical manifest must contain a list or a {pairs: [...]} object")

        report = HistoricalImportReport()
        base = manifest_path.parent

        for index, record in enumerate(records):
            if not isinstance(record, dict):
                report.conflicts.append(f"manifest record {index} is not an object")
                continue
            target_value = record.get("target")
            sources_value = record.get("sources")
            if not isinstance(target_value, str) or not isinstance(sources_value, list):
                report.conflicts.append(
                    f"manifest record {index} requires target:string and sources:list"
                )
                continue

            target = self._resolve_manifest_path(base, target_value)
            sources = [
                self._resolve_manifest_path(base, value)
                for value in sources_value
                if isinstance(value, str)
            ]
            report.target_count += 1
            report.source_count += len(sources)

            missing = [str(path) for path in [target, *sources] if not path.is_file()]
            if missing:
                report.conflicts.append(
                    f"manifest record {index} has missing files: {', '.join(missing)}"
                )
                continue
            if not sources:
                report.conflicts.append(f"manifest record {index} has no source images")
                continue

            key = str(record.get("pair_key") or record.get("design_id") or pairing_key(target))
            metadata = record.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metadata = {
                **metadata,
                "import_mode": "manifest",
                "manifest": str(manifest_path),
                "manifest_index": index,
            }
            pair = self.registry.register_pair(key, sources, target, metadata=metadata)
            report.imported_pair_ids.append(pair.pair_id)

        report.imported_pair_ids = sorted(set(report.imported_pair_ids))
        if report.imported_pair_ids:
            report.dataset = self.registry.create_dataset(dataset_name, seed=seed)
        return report

    @staticmethod
    def _resolve_manifest_path(base: Path, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base / path
        return path.resolve()
