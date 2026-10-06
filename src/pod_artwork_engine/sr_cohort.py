from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageOps

from .dataset_registry import normalized_artwork_hash
from .harness import HarnessCaseFactory, HarnessStore, load_candidate_manifest
from .harness_models import (
    BenchmarkTier,
    CandidateManifest,
    CandidateManifestEntry,
    OperationalMetrics,
    PrecisionEvidence,
    SRCohortCaseEvidence,
    SRCohortReport,
    SRCohortSpec,
)
from .settings import Settings
from .storage import StorageManager


class SRCohortError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SRCohortMaterializer:
    """Build one shared pre-SR cohort plus native/Lanczos benchmark baselines.

    Ground-truth target images are evaluation-only and are rejected as cohort
    inputs by both path and normalized artwork identity.
    """

    def __init__(self, settings: Settings, registry, store: HarnessStore) -> None:
        self.settings = settings
        self.registry = registry
        self.store = store
        self.case_factory = HarnessCaseFactory(registry)
        self.storage = StorageManager(settings)

    @staticmethod
    def _entry_for_case(
        manifest: CandidateManifest,
        *,
        pair_id: str,
        case_id: str,
        artwork_identity: str,
    ) -> CandidateManifestEntry | None:
        return (
            manifest.candidates.get(pair_id)
            or manifest.candidates.get(case_id)
            or manifest.candidates.get(artwork_identity)
        )

    @staticmethod
    def _resolve_candidate(
        entry: CandidateManifestEntry,
        manifest_base: Path,
    ) -> Path:
        path = Path(entry.result_path).expanduser()
        if not path.is_absolute():
            path = manifest_base / path
        return path.resolve()

    @staticmethod
    def _normalized_copy(source_path: Path, output_path: Path) -> tuple[int, int]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source_path) as source:
            image = ImageOps.exif_transpose(source)
            has_alpha = "A" in image.getbands() or "transparency" in source.info
            normalized = image.convert("RGBA" if has_alpha else "RGB")
            size = normalized.size
            normalized.save(output_path, format="PNG", compress_level=6)
        return size

    @staticmethod
    def _lanczos_copy(
        source_path: Path,
        output_path: Path,
        *,
        scale_factor: float,
    ) -> tuple[int, int]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source_path) as source:
            image = source.convert("RGBA" if "A" in source.getbands() else "RGB")
            width = max(1, round(image.width * scale_factor))
            height = max(1, round(image.height * scale_factor))
            resized = image.resize((width, height), Image.Resampling.LANCZOS)
            resized.save(output_path, format="PNG", compress_level=6)
        return width, height

    def _capacity_ok(
        self,
        *,
        input_width: int,
        input_height: int,
        lanczos_width: int,
        lanczos_height: int,
    ) -> bool:
        estimated_bytes = (
            input_width * input_height * 4
            + lanczos_width * lanczos_height * 4
        )
        status = self.storage.status()
        return status.used_bytes + estimated_bytes <= status.hard_limit_bytes

    @staticmethod
    def _failure_entry(
        *,
        expected_path: Path,
        spec: SRCohortSpec,
        pair_id: str,
        case_id: str,
        artwork_identity: str,
        reason: str,
    ) -> CandidateManifestEntry:
        return CandidateManifestEntry(
            result_path=str(expected_path),
            operational=OperationalMetrics(manual_review=True),
            precision=PrecisionEvidence(super_resolution_fail_closed=True),
            metadata={
                "benchmark_only": True,
                "production_execution_enabled": False,
                "fail_closed": True,
                "failure_reason": reason,
                "reason_codes": ["sr_cohort_failure"],
                "sr_cohort": {
                    "cohort_id": spec.cohort_id,
                    "pair_id": pair_id,
                    "case_id": case_id,
                    "artwork_identity": artwork_identity,
                    "lane": "failed",
                },
            },
        )

    @staticmethod
    def _success_entry(
        *,
        path: Path,
        spec: SRCohortSpec,
        pair_id: str,
        case_id: str,
        artwork_identity: str,
        lane: str,
        source_sha256: str,
        source_path: Path,
        input_width: int,
        input_height: int,
        output_width: int,
        output_height: int,
        source_metadata: dict[str, object],
    ) -> CandidateManifestEntry:
        return CandidateManifestEntry(
            result_path=str(path),
            metadata={
                "benchmark_only": True,
                "production_execution_enabled": False,
                "fail_closed": False,
                "sr_cohort": {
                    "cohort_id": spec.cohort_id,
                    "pair_id": pair_id,
                    "case_id": case_id,
                    "artwork_identity": artwork_identity,
                    "lane": lane,
                    "source_input_sha256": source_sha256,
                    "source_input_path": str(source_path),
                    "input_width": input_width,
                    "input_height": input_height,
                    "output_width": output_width,
                    "output_height": output_height,
                    "scale_factor": (
                        spec.scale_factor if lane == "lanczos" else 1.0
                    ),
                    "transform": (
                        "lanczos_resize"
                        if lane == "lanczos"
                        else "format_normalize_no_upscale"
                    ),
                },
                "source_candidate_metadata": source_metadata,
            },
        )

    def materialize(self, spec: SRCohortSpec) -> SRCohortReport:
        input_manifest_path = Path(spec.input_manifest_path).expanduser().resolve()
        if not input_manifest_path.is_file():
            raise FileNotFoundError(
                f"pre-SR input candidate manifest not found: {input_manifest_path}"
            )
        input_manifest = load_candidate_manifest(input_manifest_path)
        cases = self.case_factory.build(
            spec.dataset_id,
            spec.tier,
            limit=spec.limit,
        )
        if not cases:
            raise ValueError(
                f"dataset {spec.dataset_id} has no cases for tier {spec.tier.value}"
            )

        dataset = self.registry.get_dataset(spec.dataset_id)
        if dataset is None:
            raise KeyError(f"dataset not found: {spec.dataset_id}")
        dataset_manifest_path = Path(dataset.manifest_path)
        dataset_fingerprint = (
            _sha256(dataset_manifest_path)
            if dataset_manifest_path.is_file()
            else ""
        )

        cohort_dir = self.store.sr_cohort_dir(spec.cohort_id)
        source_dir = cohort_dir / "source"
        lanczos_dir = cohort_dir / "lanczos"
        source_manifest_path = cohort_dir / "source-candidates.json"
        native_manifest_path = cohort_dir / "native-candidates.json"
        lanczos_manifest_path = cohort_dir / "lanczos-candidates.json"

        source_entries: dict[str, CandidateManifestEntry] = {}
        native_entries: dict[str, CandidateManifestEntry] = {}
        lanczos_entries: dict[str, CandidateManifestEntry] = {}
        evidence: list[SRCohortCaseEvidence] = []
        success_count = 0
        failure_count = 0

        for case in cases:
            expected_source = source_dir / f"{case.pair_id}.png"
            expected_lanczos = lanczos_dir / f"{case.pair_id}.png"
            entry = self._entry_for_case(
                input_manifest,
                pair_id=case.pair_id,
                case_id=case.case_id,
                artwork_identity=case.artwork_identity,
            )
            reason: str | None = None
            source_path: Path | None = None
            source_sha = ""

            if entry is None:
                reason = "pre-SR input candidate missing from input manifest"
            else:
                source_path = self._resolve_candidate(
                    entry,
                    input_manifest_path.parent,
                )
                if not source_path.is_file():
                    reason = f"pre-SR input candidate file not found: {source_path}"

            if reason is None and source_path is not None:
                target_path = Path(case.target_path).expanduser().resolve()
                if source_path == target_path:
                    reason = "ground-truth target path cannot be used as pre-SR input"
                else:
                    try:
                        if (
                            normalized_artwork_hash(source_path)
                            == normalized_artwork_hash(target_path)
                        ):
                            reason = (
                                "ground-truth-equivalent image cannot be used "
                                "as pre-SR input"
                            )
                    except Exception as exc:
                        reason = f"pre-SR input validation failed: {exc}"

            if reason is not None or source_path is None or entry is None:
                failure = self._failure_entry(
                    expected_path=expected_source,
                    spec=spec,
                    pair_id=case.pair_id,
                    case_id=case.case_id,
                    artwork_identity=case.artwork_identity,
                    reason=reason or "pre-SR input unavailable",
                )
                source_entries[case.pair_id] = failure
                native_entries[case.pair_id] = failure.model_copy(deep=True)
                lanczos_entries[case.pair_id] = self._failure_entry(
                    expected_path=expected_lanczos,
                    spec=spec,
                    pair_id=case.pair_id,
                    case_id=case.case_id,
                    artwork_identity=case.artwork_identity,
                    reason=reason or "pre-SR input unavailable",
                )
                evidence.append(
                    SRCohortCaseEvidence(
                        pair_id=case.pair_id,
                        case_id=case.case_id,
                        artwork_identity=case.artwork_identity,
                        success=False,
                        fail_closed=True,
                        reasons=[reason or "pre-SR input unavailable"],
                    )
                )
                failure_count += 1
                continue

            try:
                with Image.open(source_path) as probe:
                    normalized_probe = ImageOps.exif_transpose(probe)
                    input_width, input_height = normalized_probe.size
                lanczos_width = max(1, round(input_width * spec.scale_factor))
                lanczos_height = max(1, round(input_height * spec.scale_factor))
                megapixels = (lanczos_width * lanczos_height) / 1_000_000
                if megapixels > spec.max_output_megapixels:
                    raise SRCohortError(
                        f"Lanczos output {megapixels:.2f}MP exceeds "
                        f"{spec.max_output_megapixels:g}MP safety limit"
                    )
                if not self._capacity_ok(
                    input_width=input_width,
                    input_height=input_height,
                    lanczos_width=lanczos_width,
                    lanczos_height=lanczos_height,
                ):
                    raise SRCohortError(
                        "SR cohort would exceed the global storage hard cap"
                    )

                normalized_size = self._normalized_copy(
                    source_path,
                    expected_source,
                )
                if normalized_size != (input_width, input_height):
                    raise SRCohortError(
                        "normalized native baseline changed image dimensions"
                    )
                actual_lanczos = self._lanczos_copy(
                    expected_source,
                    expected_lanczos,
                    scale_factor=spec.scale_factor,
                )
                if actual_lanczos != (lanczos_width, lanczos_height):
                    raise SRCohortError(
                        "Lanczos output dimensions do not match requested scale"
                    )

                source_sha = _sha256(expected_source)
                source_metadata = dict(entry.metadata)
                source_entry = self._success_entry(
                    path=expected_source,
                    spec=spec,
                    pair_id=case.pair_id,
                    case_id=case.case_id,
                    artwork_identity=case.artwork_identity,
                    lane="source",
                    source_sha256=source_sha,
                    source_path=source_path,
                    input_width=input_width,
                    input_height=input_height,
                    output_width=input_width,
                    output_height=input_height,
                    source_metadata=source_metadata,
                )
                native_entry = source_entry.model_copy(deep=True)
                native_entry.metadata["sr_cohort"]["lane"] = "native"
                lanczos_entry = self._success_entry(
                    path=expected_lanczos,
                    spec=spec,
                    pair_id=case.pair_id,
                    case_id=case.case_id,
                    artwork_identity=case.artwork_identity,
                    lane="lanczos",
                    source_sha256=source_sha,
                    source_path=source_path,
                    input_width=input_width,
                    input_height=input_height,
                    output_width=lanczos_width,
                    output_height=lanczos_height,
                    source_metadata=source_metadata,
                )

                source_entries[case.pair_id] = source_entry
                native_entries[case.pair_id] = native_entry
                lanczos_entries[case.pair_id] = lanczos_entry
                evidence.append(
                    SRCohortCaseEvidence(
                        pair_id=case.pair_id,
                        case_id=case.case_id,
                        artwork_identity=case.artwork_identity,
                        success=True,
                        source_path=str(expected_source),
                        source_sha256=source_sha,
                        input_width=input_width,
                        input_height=input_height,
                        lanczos_width=lanczos_width,
                        lanczos_height=lanczos_height,
                        native_path=str(expected_source),
                        lanczos_path=str(expected_lanczos),
                    )
                )
                success_count += 1
            except (OSError, ValueError, SRCohortError) as exc:
                expected_source.unlink(missing_ok=True)
                expected_lanczos.unlink(missing_ok=True)
                failure = self._failure_entry(
                    expected_path=expected_source,
                    spec=spec,
                    pair_id=case.pair_id,
                    case_id=case.case_id,
                    artwork_identity=case.artwork_identity,
                    reason=str(exc),
                )
                source_entries[case.pair_id] = failure
                native_entries[case.pair_id] = failure.model_copy(deep=True)
                lanczos_entries[case.pair_id] = self._failure_entry(
                    expected_path=expected_lanczos,
                    spec=spec,
                    pair_id=case.pair_id,
                    case_id=case.case_id,
                    artwork_identity=case.artwork_identity,
                    reason=str(exc),
                )
                evidence.append(
                    SRCohortCaseEvidence(
                        pair_id=case.pair_id,
                        case_id=case.case_id,
                        artwork_identity=case.artwork_identity,
                        success=False,
                        fail_closed=True,
                        reasons=[str(exc)],
                    )
                )
                failure_count += 1

        source_manifest = CandidateManifest(candidates=source_entries)
        native_manifest = CandidateManifest(candidates=native_entries)
        lanczos_manifest = CandidateManifest(candidates=lanczos_entries)
        self.store.save_model(source_manifest_path, source_manifest)
        self.store.save_model(native_manifest_path, native_manifest)
        self.store.save_model(lanczos_manifest_path, lanczos_manifest)

        reasons = [
            "benchmark-only pre-SR cohort; production execution remains disabled",
            "ground-truth target images are evaluation-only and rejected as cohort inputs",
        ]
        if failure_count:
            reasons.append(
                f"{failure_count} of {len(cases)} cohort cases failed closed"
            )

        report = SRCohortReport(
            cohort_id=spec.cohort_id,
            dataset_id=spec.dataset_id,
            tier=spec.tier,
            input_manifest_path=str(input_manifest_path),
            source_manifest_path=str(source_manifest_path),
            native_manifest_path=str(native_manifest_path),
            lanczos_manifest_path=str(lanczos_manifest_path),
            dataset_manifest_sha256=dataset_fingerprint,
            scale_factor=spec.scale_factor,
            case_count=len(cases),
            success_count=success_count,
            failure_count=failure_count,
            shared_input_identity=(failure_count == 0),
            cases=evidence,
            reasons=reasons,
        )
        self.store.save_model(cohort_dir / "spec.json", spec)
        self.store.save_model(cohort_dir / "report.json", report)
        return report
