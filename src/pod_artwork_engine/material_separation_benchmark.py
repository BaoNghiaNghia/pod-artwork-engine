from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

from .contracts import MaterialSeparationDisposition, MaterialSeparationEvidence
from .harness import (
    HarnessCaseFactory,
    HarnessRunner,
    HarnessStore,
    load_candidate_manifest,
    load_recipe,
)
from .harness_models import (
    BenchmarkCaseResult,
    BenchmarkTier,
    CandidateManifest,
    CandidateManifestEntry,
    HarnessRunStatus,
    MaterialSeparationBenchmarkRecommendation,
    MaterialSeparationBenchmarkReport,
    MaterialSeparationBenchmarkSpec,
    MaterialSeparationCaseEvidence,
    MaterialSeparationMaterializationReport,
    MaterialSeparationMaterializationSpec,
    OperationalMetrics,
    RunProvenance,
)
from .material_separation import _border_statistics
from .settings import Settings


class MaterialSeparationBenchmarkError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_rgba(path: Path) -> Image.Image:
    with Image.open(path) as source:
        source.load()
        return ImageOps.exif_transpose(source).convert("RGBA")


def _transparent_fraction(image: Image.Image) -> float:
    alpha = image.getchannel("A")
    working = alpha.copy()
    working.thumbnail((512, 512), Image.Resampling.LANCZOS)
    values = list(working.getdata())
    if not values:
        return 0.0
    return sum(value < 250 for value in values) / len(values)


def _simple_background_alpha(
    image: Image.Image,
    border_mean: tuple[float, float, float],
    *,
    threshold: float,
    softness: float,
) -> Image.Image:
    rgba = image.convert("RGBA")
    r, g, b, original_alpha = rgba.split()
    background = tuple(max(0, min(255, int(round(value)))) for value in border_mean)

    diffs = []
    for band, channel_background in zip((r, g, b), background, strict=True):
        diffs.append(
            band.point(
                lambda value, bg=channel_background: abs(int(value) - bg)
            )
        )
    distance = ImageChops.lighter(ImageChops.lighter(diffs[0], diffs[1]), diffs[2])
    lower = float(threshold)
    upper = lower + max(1.0, float(softness))

    def alpha_value(value: int) -> int:
        numeric = float(value)
        if numeric <= lower:
            return 0
        if numeric >= upper:
            return 255
        return int(round(255.0 * (numeric - lower) / (upper - lower)))

    separation_alpha = distance.point(alpha_value)
    combined_alpha = ImageChops.darker(original_alpha, separation_alpha)
    output = rgba.copy()
    output.putalpha(combined_alpha)
    return output


class MaterialSeparationMaterializer:
    """Create paired native/separated Golden candidates without touching production."""

    def __init__(self, settings: Settings, registry, store: HarnessStore) -> None:
        self.settings = settings
        self.registry = registry
        self.store = store
        self.case_factory = HarnessCaseFactory(registry)

    @staticmethod
    def _failure_entry(path: Path, reason: str) -> CandidateManifestEntry:
        return CandidateManifestEntry(
            result_path=str(path),
            operational=OperationalMetrics(manual_review=True),
            metadata={
                "benchmark_only": True,
                "production_execution_enabled": False,
                "fail_closed": True,
                "failure_reason": reason,
                "reason_codes": ["material_separation_materialization_failure"],
            },
        )

    @staticmethod
    def _native_entry(
        path: Path,
        *,
        spec: MaterialSeparationMaterializationSpec,
        evidence: MaterialSeparationEvidence,
    ) -> CandidateManifestEntry:
        return CandidateManifestEntry(
            result_path=str(path),
            metadata={
                "benchmark_only": True,
                "production_execution_enabled": False,
                "fail_closed": False,
                "material_separation_lane": "native_unseparated",
                "material_separation_cohort": {
                    "cohort_id": spec.cohort_id,
                    "source_run_id": spec.source_run_id,
                    "primary_index": evidence.primary_index,
                    "disposition": evidence.disposition.value,
                },
            },
        )

    @staticmethod
    def _separated_entry(
        path: Path,
        *,
        spec: MaterialSeparationMaterializationSpec,
        evidence: MaterialSeparationEvidence,
    ) -> CandidateManifestEntry:
        return CandidateManifestEntry(
            result_path=str(path),
            metadata={
                "benchmark_only": True,
                "production_execution_enabled": False,
                "fail_closed": False,
                "material_separation_lane": "separated",
                "material_separation_cohort": {
                    "cohort_id": spec.cohort_id,
                    "source_run_id": spec.source_run_id,
                    "primary_index": evidence.primary_index,
                    "disposition": evidence.disposition.value,
                },
            },
        )

    def materialize(
        self,
        spec: MaterialSeparationMaterializationSpec,
    ) -> MaterialSeparationMaterializationReport:
        if spec.tier is not BenchmarkTier.GOLDEN:
            raise MaterialSeparationBenchmarkError(
                "material separation materialization requires Golden Holdout tier"
            )
        scorecard = self.store.get_scorecard(spec.source_run_id)
        if scorecard is None:
            raise KeyError(f"scorecard not found: {spec.source_run_id}")
        if scorecard.tier is not BenchmarkTier.GOLDEN:
            raise MaterialSeparationBenchmarkError(
                "material separation source run must be Golden Holdout"
            )
        if scorecard.status is not HarnessRunStatus.COMPLETE:
            raise MaterialSeparationBenchmarkError(
                "material separation source run must be complete"
            )
        if scorecard.dataset_id != spec.dataset_id:
            raise MaterialSeparationBenchmarkError(
                "material separation source run dataset mismatch"
            )
        if not scorecard.provenance.dataset_manifest_sha256:
            raise MaterialSeparationBenchmarkError(
                "material separation source run dataset fingerprint unavailable"
            )

        dataset = self.registry.get_dataset(spec.dataset_id)
        if dataset is None:
            raise KeyError(f"dataset not found: {spec.dataset_id}")
        dataset_path = Path(dataset.manifest_path)
        dataset_fingerprint = _sha256(dataset_path)
        if dataset_fingerprint != scorecard.provenance.dataset_manifest_sha256:
            raise MaterialSeparationBenchmarkError(
                "material separation source run dataset fingerprint mismatch"
            )

        cases = self.case_factory.build(
            spec.dataset_id,
            BenchmarkTier.GOLDEN,
            limit=spec.limit,
        )
        if not cases:
            raise MaterialSeparationBenchmarkError(
                "dataset has no Golden Holdout material-separation cases"
            )
        results = {
            result.case_id: result
            for result in self.store.get_results(spec.source_run_id)
        }

        cohort_dir = self.store.material_separation_cohort_dir(spec.cohort_id)
        native_dir = cohort_dir / "native"
        separated_dir = cohort_dir / "separated"
        native_manifest_path = cohort_dir / "native-candidates.json"
        separated_manifest_path = cohort_dir / "separated-candidates.json"

        native_entries: dict[str, CandidateManifestEntry] = {}
        separated_entries: dict[str, CandidateManifestEntry] = {}
        evidence_cases: list[MaterialSeparationCaseEvidence] = []
        success_count = 0
        existing_alpha_count = 0
        simple_border_count = 0
        fail_closed_count = 0

        for case in cases:
            native_path = native_dir / f"{case.pair_id}.png"
            separated_path = separated_dir / f"{case.pair_id}.png"
            result = results.get(case.case_id)
            reason: str | None = None
            evidence: MaterialSeparationEvidence | None = None

            if result is None:
                reason = "source Golden case result unavailable"
            else:
                raw = result.metadata.get("material_separation")
                if not isinstance(raw, dict) or not raw:
                    reason = "material separation evidence unavailable"
                else:
                    try:
                        evidence = MaterialSeparationEvidence.model_validate(raw)
                    except ValueError as exc:
                        reason = f"invalid material separation evidence: {exc}"

            if (
                reason is None
                and evidence is not None
                and evidence.primary_index >= len(case.source_paths)
            ):
                reason = "material separation primary index outside source paths"

            source_path: Path | None = None
            native_saved = False
            native_image: Image.Image | None = None
            if evidence is not None and evidence.primary_index < len(case.source_paths):
                source_path = Path(case.source_paths[evidence.primary_index]).resolve()
                try:
                    native_image = _load_rgba(source_path)
                    native_path.parent.mkdir(parents=True, exist_ok=True)
                    native_image.save(native_path, format="PNG", compress_level=6)
                    native_entries[case.pair_id] = self._native_entry(
                        native_path,
                        spec=spec,
                        evidence=evidence,
                    )
                    native_saved = True
                except OSError as exc:
                    reason = f"native source unavailable: {type(exc).__name__}"

            if not native_saved:
                native_entries[case.pair_id] = self._failure_entry(
                    native_path,
                    reason or "native source unavailable",
                )

            disposition = (
                evidence.disposition
                if evidence is not None
                else MaterialSeparationDisposition.MANUAL_REVIEW
            )
            can_execute = (
                reason is None
                and evidence is not None
                and native_image is not None
                and not evidence.fail_closed
                and evidence.confidence >= spec.min_evidence_confidence
                and disposition
                in {
                    MaterialSeparationDisposition.EXISTING_ALPHA,
                    MaterialSeparationDisposition.SIMPLE_BORDER_BACKGROUND,
                }
            )
            if not can_execute:
                failure_reason = reason or "material separation evidence fails execution gate"
                if evidence is not None and evidence.fail_closed:
                    failure_reason = (
                        f"material separation disposition {evidence.disposition.value} "
                        "is fail-closed"
                    )
                elif evidence is not None and evidence.confidence < spec.min_evidence_confidence:
                    failure_reason = "material separation evidence confidence below benchmark floor"
                separated_entries[case.pair_id] = self._failure_entry(
                    separated_path,
                    failure_reason,
                )
                fail_closed_count += 1
                evidence_cases.append(
                    MaterialSeparationCaseEvidence(
                        pair_id=case.pair_id,
                        case_id=case.case_id,
                        artwork_identity=case.artwork_identity,
                        disposition=disposition.value,
                        source_path=str(source_path) if source_path is not None else "",
                        native_path=str(native_path) if native_saved else None,
                        evidence_confidence=evidence.confidence if evidence is not None else 0,
                        border_uniformity=evidence.border_uniformity if evidence is not None else 0,
                        edge_contact_ratio=evidence.edge_contact_ratio if evidence is not None else 0,
                        foreground_contrast=evidence.foreground_contrast if evidence is not None else 0,
                        native_transparent_fraction=(
                            _transparent_fraction(native_image)
                            if native_image is not None
                            else 0
                        ),
                        fail_closed=True,
                        reasons=[failure_reason],
                    )
                )
                continue

            assert evidence is not None
            assert native_image is not None
            assert source_path is not None
            try:
                if disposition is MaterialSeparationDisposition.EXISTING_ALPHA:
                    separated_image = native_image.copy()
                    existing_alpha_count += 1
                else:
                    if evidence.border_uniformity < 0.90:
                        raise MaterialSeparationBenchmarkError(
                            "simple border disposition has weak border uniformity"
                        )
                    if evidence.edge_contact_ratio > 0:
                        raise MaterialSeparationBenchmarkError(
                            "simple border disposition touches source edge"
                        )
                    border_mean, _ = _border_statistics(native_image)
                    separated_image = _simple_background_alpha(
                        native_image,
                        border_mean,
                        threshold=spec.color_distance_threshold,
                        softness=spec.color_distance_softness,
                    )
                    simple_border_count += 1

                separated_path.parent.mkdir(parents=True, exist_ok=True)
                separated_image.save(
                    separated_path,
                    format="PNG",
                    compress_level=6,
                )
                separated_entries[case.pair_id] = self._separated_entry(
                    separated_path,
                    spec=spec,
                    evidence=evidence,
                )
                evidence_cases.append(
                    MaterialSeparationCaseEvidence(
                        pair_id=case.pair_id,
                        case_id=case.case_id,
                        artwork_identity=case.artwork_identity,
                        success=True,
                        disposition=disposition.value,
                        source_path=str(source_path),
                        native_path=str(native_path),
                        separated_path=str(separated_path),
                        evidence_confidence=evidence.confidence,
                        border_uniformity=evidence.border_uniformity,
                        edge_contact_ratio=evidence.edge_contact_ratio,
                        foreground_contrast=evidence.foreground_contrast,
                        native_transparent_fraction=_transparent_fraction(native_image),
                        separated_transparent_fraction=_transparent_fraction(
                            separated_image
                        ),
                    )
                )
                success_count += 1
            except (OSError, ValueError, MaterialSeparationBenchmarkError) as exc:
                failure_reason = f"{type(exc).__name__}: {exc}"
                separated_entries[case.pair_id] = self._failure_entry(
                    separated_path,
                    failure_reason,
                )
                fail_closed_count += 1
                evidence_cases.append(
                    MaterialSeparationCaseEvidence(
                        pair_id=case.pair_id,
                        case_id=case.case_id,
                        artwork_identity=case.artwork_identity,
                        disposition=disposition.value,
                        source_path=str(source_path),
                        native_path=str(native_path) if native_saved else None,
                        evidence_confidence=evidence.confidence,
                        border_uniformity=evidence.border_uniformity,
                        edge_contact_ratio=evidence.edge_contact_ratio,
                        foreground_contrast=evidence.foreground_contrast,
                        native_transparent_fraction=_transparent_fraction(native_image),
                        fail_closed=True,
                        reasons=[failure_reason],
                    )
                )

        self.store.save_model(
            native_manifest_path,
            CandidateManifest(candidates=native_entries),
        )
        self.store.save_model(
            separated_manifest_path,
            CandidateManifest(candidates=separated_entries),
        )
        report = MaterialSeparationMaterializationReport(
            cohort_id=spec.cohort_id,
            dataset_id=spec.dataset_id,
            source_run_id=spec.source_run_id,
            tier=BenchmarkTier.GOLDEN,
            dataset_manifest_sha256=dataset_fingerprint,
            native_manifest_path=str(native_manifest_path),
            separated_manifest_path=str(separated_manifest_path),
            case_count=len(cases),
            success_count=success_count,
            failure_count=len(cases) - success_count,
            existing_alpha_count=existing_alpha_count,
            simple_border_count=simple_border_count,
            fail_closed_count=fail_closed_count,
            cases=evidence_cases,
            reasons=["production_material_separation_execution_disabled"],
        )
        self.store.save_model(cohort_dir / "report.json", report)
        return report


class MaterialSeparationBenchmarkMatrixRunner:
    def __init__(self, settings: Settings, registry, store: HarnessStore) -> None:
        self.settings = settings
        self.registry = registry
        self.store = store

    @staticmethod
    def _validate_manifest(
        manifest: CandidateManifest,
        *,
        expected_lane: str,
    ) -> None:
        for key, entry in manifest.candidates.items():
            if entry.metadata.get("benchmark_only") is not True:
                raise MaterialSeparationBenchmarkError(
                    f"material separation candidate {key} is not benchmark-only"
                )
            if entry.metadata.get("production_execution_enabled") is True:
                raise MaterialSeparationBenchmarkError(
                    f"material separation candidate {key} enables production execution"
                )
            if entry.metadata.get("fail_closed") is True:
                continue
            if entry.metadata.get("material_separation_lane") != expected_lane:
                raise MaterialSeparationBenchmarkError(
                    f"material separation candidate {key} lane mismatch"
                )

    @staticmethod
    def _cohort_signature(
        result: BenchmarkCaseResult,
    ) -> tuple[str, str, int, str] | None:
        raw = result.metadata.get("material_separation_cohort")
        if not isinstance(raw, dict):
            return None
        cohort_id = raw.get("cohort_id")
        source_run_id = raw.get("source_run_id")
        primary_index = raw.get("primary_index")
        disposition = raw.get("disposition")
        if not isinstance(cohort_id, str) or not cohort_id:
            return None
        if not isinstance(source_run_id, str) or not source_run_id:
            return None
        if not isinstance(primary_index, int):
            return None
        if not isinstance(disposition, str) or not disposition:
            return None
        return cohort_id, source_run_id, primary_index, disposition

    @classmethod
    def _same_source(
        cls,
        native: BenchmarkCaseResult,
        separated: BenchmarkCaseResult,
    ) -> bool:
        native_signature = cls._cohort_signature(native)
        separated_signature = cls._cohort_signature(separated)
        return (
            native_signature is not None
            and separated_signature is not None
            and native_signature == separated_signature
        )

    @staticmethod
    def _mean(values: list[float | None]) -> float | None:
        available = [value for value in values if value is not None]
        return sum(available) / len(available) if available else None

    def run(
        self,
        spec: MaterialSeparationBenchmarkSpec,
        *,
        spec_base: Path | None = None,
    ) -> MaterialSeparationBenchmarkReport:
        if spec.tier is not BenchmarkTier.GOLDEN:
            raise MaterialSeparationBenchmarkError(
                "material separation matrix requires Golden Holdout tier"
            )
        base = spec_base or Path.cwd()
        recipe_path = Path(spec.recipe_path).expanduser()
        native_path = Path(spec.native_manifest_path).expanduser()
        separated_path = Path(spec.separated_manifest_path).expanduser()
        if not recipe_path.is_absolute():
            recipe_path = base / recipe_path
        if not native_path.is_absolute():
            native_path = base / native_path
        if not separated_path.is_absolute():
            separated_path = base / separated_path

        recipe = load_recipe(recipe_path.resolve())
        native_manifest = load_candidate_manifest(native_path.resolve())
        separated_manifest = load_candidate_manifest(separated_path.resolve())
        self._validate_manifest(native_manifest, expected_lane="native_unseparated")
        self._validate_manifest(separated_manifest, expected_lane="separated")

        runner = HarnessRunner(self.registry, self.store)
        native_score = runner.run_candidates(
            spec.dataset_id,
            BenchmarkTier.GOLDEN,
            recipe.model_copy(
                deep=True,
                update={
                    "recipe_id": (
                        f"{recipe.recipe_id}__material_separation_native"
                    )
                },
            ),
            native_manifest,
            manifest_base=native_path.resolve().parent,
            limit=spec.limit,
            provenance=RunProvenance(
                execution_kind="material_separation_benchmark:native"
            ),
        )
        separated_score = runner.run_candidates(
            spec.dataset_id,
            BenchmarkTier.GOLDEN,
            recipe.model_copy(
                deep=True,
                update={
                    "recipe_id": (
                        f"{recipe.recipe_id}__material_separation_challenger"
                    )
                },
            ),
            separated_manifest,
            manifest_base=separated_path.resolve().parent,
            limit=spec.limit,
            provenance=RunProvenance(
                execution_kind="material_separation_benchmark:separated"
            ),
        )
        if (
            native_score.provenance.dataset_manifest_sha256
            != separated_score.provenance.dataset_manifest_sha256
        ):
            raise MaterialSeparationBenchmarkError(
                "native/separated Harness dataset fingerprint mismatch"
            )

        native_results = {
            result.case_id: result
            for result in self.store.get_results(native_score.run_id)
        }
        separated_results = {
            result.case_id: result
            for result in self.store.get_results(separated_score.run_id)
        }
        comparable = [
            (native_results[case_id], separated_results[case_id])
            for case_id in sorted(set(native_results) & set(separated_results))
            if native_results[case_id].success
            and separated_results[case_id].success
            and self._same_source(
                native_results[case_id],
                separated_results[case_id],
            )
        ]

        quality_delta = self._mean(
            [
                separated.quality_score - native.quality_score
                for native, separated in comparable
                if native.quality_score is not None
                and separated.quality_score is not None
            ]
        )
        semantic_delta = self._mean(
            [
                separated.semantic_score - native.semantic_score
                for native, separated in comparable
                if native.semantic_score is not None
                and separated.semantic_score is not None
            ]
        )
        technical_delta = self._mean(
            [
                separated.technical_score - native.technical_score
                for native, separated in comparable
                if native.technical_score is not None
                and separated.technical_score is not None
            ]
        )
        alpha_delta = self._mean(
            [
                separated.technical.alpha - native.technical.alpha
                for native, separated in comparable
                if native.technical.alpha is not None
                and separated.technical.alpha is not None
            ]
        )
        halo_delta = self._mean(
            [
                separated.technical.halo_aliasing
                - native.technical.halo_aliasing
                for native, separated in comparable
                if native.technical.halo_aliasing is not None
                and separated.technical.halo_aliasing is not None
            ]
        )
        small_detail_delta = self._mean(
            [
                separated.technical.small_detail_survival
                - native.technical.small_detail_survival
                for native, separated in comparable
                if native.technical.small_detail_survival is not None
                and separated.technical.small_detail_survival is not None
            ]
        )
        failure_delta = separated_score.failure_rate - native_score.failure_rate
        manual_delta = (
            separated_score.manual_review_rate - native_score.manual_review_rate
        )

        reasons: list[str] = []
        enough = len(comparable) >= spec.min_comparable_cases
        if not enough:
            reasons.append("insufficient_comparable_golden_cases")
        if quality_delta is None:
            reasons.append("material_separation_quality_delta_unavailable")
        if technical_delta is None:
            reasons.append("material_separation_technical_delta_unavailable")
        if alpha_delta is None:
            reasons.append("material_separation_alpha_delta_unavailable")
        if semantic_delta is None:
            reasons.append("material_separation_semantic_delta_unavailable")
        if small_detail_delta is None:
            reasons.append("material_separation_small_detail_delta_unavailable")
        if halo_delta is None:
            reasons.append("material_separation_halo_delta_unavailable")

        safety_regression = False
        if failure_delta > spec.max_failure_rate_increase:
            reasons.append("material_separation_failure_rate_regression")
            safety_regression = True
        if manual_delta > spec.max_manual_review_rate_increase:
            reasons.append("material_separation_manual_review_rate_regression")
            safety_regression = True
        if (
            semantic_delta is not None
            and semantic_delta < -spec.max_semantic_drop
        ):
            reasons.append("material_separation_semantic_regression")
            safety_regression = True
        if (
            technical_delta is not None
            and technical_delta < spec.min_technical_gain
        ):
            reasons.append("material_separation_technical_regression")
            safety_regression = True
        if (
            small_detail_delta is not None
            and small_detail_delta < -spec.max_small_detail_drop
        ):
            reasons.append("material_separation_small_detail_regression")
            safety_regression = True
        if halo_delta is not None and halo_delta < -spec.max_halo_drop:
            reasons.append("material_separation_halo_regression")
            safety_regression = True

        benefit = (
            (quality_delta is not None and quality_delta >= spec.min_quality_gain)
            or (alpha_delta is not None and alpha_delta >= spec.min_alpha_gain)
        )
        if not benefit:
            reasons.append("material_separation_gain_below_floor")

        structural_missing = any(
            reason.endswith("_unavailable")
            or reason == "insufficient_comparable_golden_cases"
            for reason in reasons
        )
        if safety_regression:
            recommendation = MaterialSeparationBenchmarkRecommendation.MANUAL_REVIEW
        elif structural_missing:
            recommendation = (
                MaterialSeparationBenchmarkRecommendation.INSUFFICIENT_EVIDENCE
            )
        elif benefit:
            recommendation = (
                MaterialSeparationBenchmarkRecommendation
                .MATERIAL_SEPARATION_FOR_HUMAN_REVIEW
            )
        else:
            recommendation = MaterialSeparationBenchmarkRecommendation.KEEP_NATIVE

        report = MaterialSeparationBenchmarkReport(
            matrix_id=spec.matrix_id,
            dataset_id=spec.dataset_id,
            tier=BenchmarkTier.GOLDEN,
            recipe_id=recipe.recipe_id,
            recipe_version=recipe.version,
            dataset_manifest_sha256=(
                native_score.provenance.dataset_manifest_sha256
            ),
            native_run_id=native_score.run_id,
            separated_run_id=separated_score.run_id,
            native_scorecard_id=native_score.scorecard_id,
            separated_scorecard_id=separated_score.scorecard_id,
            comparable_case_count=len(comparable),
            quality_delta=(
                round(quality_delta, 8) if quality_delta is not None else None
            ),
            semantic_delta=(
                round(semantic_delta, 8) if semantic_delta is not None else None
            ),
            technical_delta=(
                round(technical_delta, 8) if technical_delta is not None else None
            ),
            alpha_delta=(
                round(alpha_delta, 8) if alpha_delta is not None else None
            ),
            halo_delta=(
                round(halo_delta, 8) if halo_delta is not None else None
            ),
            small_detail_delta=(
                round(small_detail_delta, 8)
                if small_detail_delta is not None
                else None
            ),
            failure_rate_delta=round(failure_delta, 8),
            manual_review_rate_delta=round(manual_delta, 8),
            recommendation=recommendation,
            sufficient_evidence=(
                enough and not structural_missing and not safety_regression
            ),
            reasons=list(
                dict.fromkeys(
                    reasons
                    + ["production_material_separation_execution_disabled"]
                )
            ),
        )
        matrix_dir = self.store.material_separation_matrix_dir(spec.matrix_id)
        self.store.save_model(matrix_dir / "report.json", report)
        return report
