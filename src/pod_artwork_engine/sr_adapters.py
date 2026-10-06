from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import psutil
from PIL import Image

from .contracts import ProviderAction, ProviderRequest, QualityMode
from .harness import HarnessCaseFactory, HarnessStore, load_candidate_manifest
from .harness_models import (
    BenchmarkTier,
    CandidateManifest,
    CandidateManifestEntry,
    OperationalMetrics,
    PrecisionEvidence,
    SRAdapterKind,
    SRAdapterRunReport,
    SRAdapterSpec,
)
from .providers import (
    ProviderProtocolError,
    ProviderUnavailable,
    RemoteProvider,
    materialize_provider_candidate,
)
from .settings import MIB, Settings
from .storage import StorageManager
from .typed_control import TypedBoundaryError, validate_typed_payload


class SRAdapterUnavailable(RuntimeError):
    pass


class SRAdapterExecutionError(RuntimeError):
    pass


def load_sr_adapter_spec(path: Path) -> SRAdapterSpec:
    try:
        return validate_typed_payload(
            SRAdapterSpec,
            path.read_text(encoding="utf-8"),
        )
    except (OSError, TypedBoundaryError) as exc:
        raise SRAdapterUnavailable(f"invalid SR adapter spec: {exc}") from exc


def _numeric_metadata(metadata: dict[str, object], key: str) -> float:
    value = metadata.get(key)
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    return 0.0


class SRAdapterMaterializer:
    """Materialize real benchmark-only SR candidates into CandidateManifest.

    This class intentionally lives outside Engine and RouterPolicy. It consumes
    an existing candidate manifest, calls a concrete local/remote SR backend,
    validates the produced image, and writes a new manifest for Phase 2H.
    """

    def __init__(self, settings: Settings, registry, store: HarnessStore) -> None:
        self.settings = settings
        self.registry = registry
        self.store = store
        self.case_factory = HarnessCaseFactory(registry)
        self.remote_provider = RemoteProvider(settings)
        self.storage = StorageManager(settings)

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
    def _local_executable(spec: SRAdapterSpec) -> str | None:
        if not spec.command:
            return None
        executable = spec.command[0]
        path = Path(executable).expanduser()
        if path.is_absolute() or "/" in executable or "\\" in executable:
            return str(path.resolve()) if path.is_file() else None
        return shutil.which(executable)

    def _backend_status(self, spec: SRAdapterSpec) -> tuple[bool, str | None]:
        if spec.kind is SRAdapterKind.LOCAL_COMMAND:
            executable = self._local_executable(spec)
            if executable is None:
                return False, "local SR executable is not available"
            return True, None

        if not self.remote_provider.available:
            return False, "remote provider URL is not configured"
        try:
            recipe = self.remote_provider.recipe()
        except ProviderProtocolError as exc:
            return False, str(exc)
        action = recipe.for_action(ProviderAction.SUPER_RESOLUTION)
        if action is None:
            return False, "provider super_resolution action is not explicitly configured"
        if not action.enabled:
            return False, "provider super_resolution action is disabled"
        return True, None

    @staticmethod
    def _command_tokens(
        spec: SRAdapterSpec,
        *,
        input_path: Path,
        output_path: Path,
        pair_id: str,
        case_id: str,
    ) -> list[str]:
        replacements = {
            "{input}": str(input_path),
            "{output}": str(output_path),
            "{scale}": f"{spec.scale_factor:g}",
            "{pair_id}": pair_id,
            "{case_id}": case_id,
        }
        tokens: list[str] = []
        for token in spec.command:
            value = token
            for placeholder, replacement in replacements.items():
                value = value.replace(placeholder, replacement)
            tokens.append(value)
        return tokens

    @staticmethod
    def _terminate_process_tree(process: psutil.Process) -> None:
        try:
            children = process.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            children = []
        for child in reversed(children):
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    @staticmethod
    def _process_rss_bytes(process: psutil.Process) -> int:
        total = 0
        try:
            total += process.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0
        try:
            children = process.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            children = []
        for child in children:
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return total

    def _preflight_capacity(
        self,
        spec: SRAdapterSpec,
        input_path: Path,
    ) -> None:
        try:
            with Image.open(input_path) as image:
                width, height = image.size
        except Exception as exc:
            raise SRAdapterExecutionError(
                f"SR input is not a valid image: {exc}"
            ) from exc

        expected_width = max(1, round(width * spec.scale_factor))
        expected_height = max(1, round(height * spec.scale_factor))
        expected_megapixels = (expected_width * expected_height) / 1_000_000
        if expected_megapixels > spec.max_output_megapixels:
            raise SRAdapterExecutionError(
                f"requested SR output {expected_megapixels:.2f}MP exceeds "
                f"{spec.max_output_megapixels:g}MP safety limit"
            )

        estimated_bytes = expected_width * expected_height * 4
        storage = self.storage.status()
        if storage.used_bytes + estimated_bytes > storage.hard_limit_bytes:
            raise SRAdapterExecutionError(
                "SR candidate would exceed the global storage hard cap"
            )

    def _run_local(
        self,
        spec: SRAdapterSpec,
        *,
        input_path: Path,
        output_path: Path,
        pair_id: str,
        case_id: str,
    ) -> tuple[float, float]:
        tokens = self._command_tokens(
            spec,
            input_path=input_path,
            output_path=output_path,
            pair_id=pair_id,
            case_id=case_id,
        )
        executable = self._local_executable(spec)
        if executable is None:
            raise SRAdapterUnavailable("local SR executable is not available")
        tokens[0] = executable
        output_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        try:
            process = subprocess.Popen(
                tokens,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise SRAdapterUnavailable(
                f"local SR process could not start: {exc}"
            ) from exc

        peak_rss = 0
        ps_process = psutil.Process(process.pid)
        timed_out = False
        while process.poll() is None:
            peak_rss = max(peak_rss, self._process_rss_bytes(ps_process))
            if time.perf_counter() - started > spec.timeout_seconds:
                timed_out = True
                self._terminate_process_tree(ps_process)
                break
            time.sleep(0.05)
        process.wait()
        elapsed_ms = (time.perf_counter() - started) * 1000
        peak_rss = max(peak_rss, self._process_rss_bytes(ps_process))

        if timed_out:
            raise SRAdapterExecutionError(
                f"local SR process exceeded {spec.timeout_seconds:g}s timeout"
            )
        if process.returncode != 0:
            raise SRAdapterExecutionError(
                f"local SR process exited with code {process.returncode}"
            )
        return elapsed_ms, peak_rss / MIB

    def _run_remote(
        self,
        spec: SRAdapterSpec,
        *,
        input_path: Path,
        output_path: Path,
        pair_id: str,
        quality_mode: QualityMode,
    ):
        started = time.perf_counter()
        try:
            result = self.remote_provider.execute(
                ProviderRequest(
                    action=ProviderAction.SUPER_RESOLUTION,
                    job_id=f"sr-benchmark-{pair_id}",
                    quality_mode=quality_mode,
                    source_paths=[str(input_path)],
                    requested_capabilities=[
                        "super_resolution",
                        f"scale:{spec.scale_factor:g}",
                    ],
                )
            )
            candidate = materialize_provider_candidate(result, output_path)
        except (ProviderUnavailable, ProviderProtocolError) as exc:
            raise SRAdapterUnavailable(str(exc)) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000
        if candidate is None:
            raise SRAdapterExecutionError(
                "remote SR provider returned no candidate image"
            )
        return result, candidate, elapsed_ms

    @staticmethod
    def _validate_image(
        spec: SRAdapterSpec,
        *,
        input_path: Path,
        output_path: Path,
    ) -> tuple[tuple[int, int], tuple[int, int], float]:
        try:
            with Image.open(input_path) as image:
                input_size = image.size
            with Image.open(output_path) as image:
                image.verify()
            with Image.open(output_path) as image:
                output_size = image.size
        except Exception as exc:
            output_path.unlink(missing_ok=True)
            raise SRAdapterExecutionError(
                f"SR output is not a valid image: {exc}"
            ) from exc

        input_width, input_height = input_size
        output_width, output_height = output_size
        if input_width <= 0 or input_height <= 0:
            raise SRAdapterExecutionError("SR input has invalid dimensions")
        if output_width <= 0 or output_height <= 0:
            output_path.unlink(missing_ok=True)
            raise SRAdapterExecutionError("SR output has invalid dimensions")

        megapixels = (output_width * output_height) / 1_000_000
        if megapixels > spec.max_output_megapixels:
            output_path.unlink(missing_ok=True)
            raise SRAdapterExecutionError(
                f"SR output {megapixels:.2f}MP exceeds "
                f"{spec.max_output_megapixels:g}MP safety limit"
            )

        width_scale = output_width / input_width
        height_scale = output_height / input_height
        measured_scale = min(width_scale, height_scale)
        if measured_scale < spec.min_output_scale:
            output_path.unlink(missing_ok=True)
            raise SRAdapterExecutionError(
                f"SR output scale {measured_scale:.3f} is below "
                f"minimum {spec.min_output_scale:.3f}"
            )

        scale_delta = abs(width_scale - height_scale)
        if scale_delta > 0.03:
            output_path.unlink(missing_ok=True)
            raise SRAdapterExecutionError(
                "SR output changed aspect ratio beyond 3% scale tolerance"
            )

        return input_size, output_size, measured_scale

    @staticmethod
    def _failure_entry(
        *,
        expected_path: Path,
        source_entry: CandidateManifestEntry | None,
        spec: SRAdapterSpec,
        reason: str,
        backend_available: bool,
    ) -> CandidateManifestEntry:
        metadata = {
            "benchmark_only": True,
            "production_execution_enabled": False,
            "fail_closed": True,
            "failure_reason": reason,
            "reason_codes": ["sr_adapter_failure"],
            "sr_adapter": {
                "adapter_id": spec.adapter_id,
                "version": spec.version,
                "kind": spec.kind.value,
                "model_alias": spec.model_alias,
                "requested_scale": spec.scale_factor,
                "backend_available": backend_available,
            },
        }
        if source_entry is not None:
            metadata["source_candidate_metadata"] = dict(source_entry.metadata)
        return CandidateManifestEntry(
            result_path=str(expected_path),
            operational=OperationalMetrics(
                manual_review=True,
            ),
            precision=PrecisionEvidence(
                super_resolution_fail_closed=True,
            ),
            metadata=metadata,
        )

    def materialize(
        self,
        *,
        dataset_id: str,
        tier: BenchmarkTier,
        source_manifest_path: Path,
        adapter_spec: SRAdapterSpec,
        quality_mode: QualityMode = QualityMode.PRINT_READY,
        limit: int | None = None,
        output_manifest_path: Path | None = None,
    ) -> SRAdapterRunReport:
        source_manifest_path = source_manifest_path.expanduser().resolve()
        source_manifest = load_candidate_manifest(source_manifest_path)
        cases = self.case_factory.build(dataset_id, tier, limit=limit)
        if not cases:
            raise ValueError(
                f"dataset {dataset_id} has no cases for tier {tier.value}"
            )

        report_seed = SRAdapterRunReport(
            dataset_id=dataset_id,
            tier=tier,
            quality_mode=quality_mode,
            adapter_id=adapter_spec.adapter_id,
            adapter_version=adapter_spec.version,
            adapter_kind=adapter_spec.kind,
            model_alias=adapter_spec.model_alias,
            source_manifest_path=str(source_manifest_path),
            output_manifest_path="pending",
        )
        run_dir = self.store.sr_adapter_dir(report_seed.run_id)
        candidates_dir = run_dir / "candidates"
        candidates_dir.mkdir(parents=True, exist_ok=True)
        output_manifest = (
            output_manifest_path.expanduser().resolve()
            if output_manifest_path is not None
            else run_dir / "candidates.json"
        )

        backend_available, backend_reason = self._backend_status(adapter_spec)
        entries: dict[str, CandidateManifestEntry] = {}
        success_count = 0
        failure_count = 0

        for case in cases:
            source_entry = self._entry_for_case(
                source_manifest,
                pair_id=case.pair_id,
                case_id=case.case_id,
                artwork_identity=case.artwork_identity,
            )
            expected_output = candidates_dir / f"{case.pair_id}.png"

            if source_entry is None:
                entries[case.pair_id] = self._failure_entry(
                    expected_path=expected_output,
                    source_entry=None,
                    spec=adapter_spec,
                    reason="source candidate missing from input manifest",
                    backend_available=backend_available,
                )
                failure_count += 1
                continue

            input_path = self._resolve_candidate(
                source_entry,
                source_manifest_path.parent,
            )
            if not input_path.is_file():
                entries[case.pair_id] = self._failure_entry(
                    expected_path=expected_output,
                    source_entry=source_entry,
                    spec=adapter_spec,
                    reason=f"source candidate file not found: {input_path}",
                    backend_available=backend_available,
                )
                failure_count += 1
                continue

            if not backend_available:
                entries[case.pair_id] = self._failure_entry(
                    expected_path=expected_output,
                    source_entry=source_entry,
                    spec=adapter_spec,
                    reason=backend_reason or "SR backend unavailable",
                    backend_available=False,
                )
                failure_count += 1
                continue

            try:
                self._preflight_capacity(adapter_spec, input_path)
                remote_result = None
                if adapter_spec.kind is SRAdapterKind.LOCAL_COMMAND:
                    latency_ms, peak_ram_mb = self._run_local(
                        adapter_spec,
                        input_path=input_path,
                        output_path=expected_output,
                        pair_id=case.pair_id,
                        case_id=case.case_id,
                    )
                    candidate_path = expected_output
                    provider_calls = 0
                    peak_vram_mb = 0.0
                    cost_usd = adapter_spec.estimated_cost_usd
                    recognized_text: list[str] = []
                    semantic_judge = None
                    backend_metadata: dict[str, object] = {}
                else:
                    remote_result, candidate_path, latency_ms = self._run_remote(
                        adapter_spec,
                        input_path=input_path,
                        output_path=expected_output,
                        pair_id=case.pair_id,
                        quality_mode=quality_mode,
                    )
                    backend_metadata = dict(remote_result.metadata)
                    provider_calls = 1
                    peak_ram_mb = _numeric_metadata(
                        backend_metadata,
                        "peak_ram_mb",
                    )
                    peak_vram_mb = _numeric_metadata(
                        backend_metadata,
                        "peak_vram_mb",
                    )
                    cost_usd = _numeric_metadata(
                        backend_metadata,
                        "cost_usd",
                    )
                    if cost_usd <= 0:
                        cost_usd = adapter_spec.estimated_cost_usd
                    recognized_text = list(remote_result.recognized_text)
                    semantic_judge = remote_result.judge_result

                input_size, output_size, measured_scale = self._validate_image(
                    adapter_spec,
                    input_path=input_path,
                    output_path=candidate_path,
                )

                hallucination_risk = (
                    backend_metadata.get("hallucination_risk") is True
                )
                reason_codes = backend_metadata.get("reason_codes")
                safe_reason_codes = (
                    [str(value) for value in reason_codes]
                    if isinstance(reason_codes, list)
                    else []
                )
                source_cohort = source_entry.metadata.get("sr_cohort")
                metadata = {
                    "benchmark_only": True,
                    "production_execution_enabled": False,
                    "fail_closed": False,
                    "hallucination_risk": hallucination_risk,
                    "reason_codes": safe_reason_codes,
                    "sr_adapter": {
                        "adapter_id": adapter_spec.adapter_id,
                        "version": adapter_spec.version,
                        "kind": adapter_spec.kind.value,
                        "model_alias": (
                            remote_result.model_version
                            if remote_result is not None
                            and remote_result.model_version
                            else adapter_spec.model_alias
                        ),
                        "requested_scale": adapter_spec.scale_factor,
                        "measured_scale": measured_scale,
                        "input_width": input_size[0],
                        "input_height": input_size[1],
                        "output_width": output_size[0],
                        "output_height": output_size[1],
                        "backend_available": True,
                    },
                    "backend_metadata": backend_metadata,
                    "source_candidate_metadata": dict(source_entry.metadata),
                }
                if isinstance(source_cohort, dict):
                    metadata["sr_cohort"] = dict(source_cohort)
                    metadata["sr_cohort"]["lane"] = adapter_spec.kind.value
                entries[case.pair_id] = CandidateManifestEntry(
                    result_path=str(candidate_path),
                    recognized_text=recognized_text,
                    semantic_judge=semantic_judge,
                    operational=OperationalMetrics(
                        latency_ms=latency_ms,
                        peak_ram_mb=peak_ram_mb,
                        peak_vram_mb=peak_vram_mb,
                        provider_calls=provider_calls,
                        cost_usd=cost_usd,
                        manual_review=False,
                    ),
                    precision=PrecisionEvidence(
                        super_resolution_fail_closed=False,
                    ),
                    metadata=metadata,
                )
                success_count += 1
            except (SRAdapterUnavailable, SRAdapterExecutionError) as exc:
                expected_output.unlink(missing_ok=True)
                entries[case.pair_id] = self._failure_entry(
                    expected_path=expected_output,
                    source_entry=source_entry,
                    spec=adapter_spec,
                    reason=str(exc),
                    backend_available=backend_available,
                )
                failure_count += 1

        manifest = CandidateManifest(candidates=entries)
        self.store.save_model(output_manifest, manifest)

        reasons: list[str] = []
        if not backend_available:
            reasons.append(backend_reason or "SR backend unavailable")
        if failure_count:
            reasons.append(
                f"{failure_count} of {len(cases)} SR candidate cases failed closed"
            )
        reasons.append(
            "benchmark-only adapter output; production SR execution remains disabled"
        )

        report = report_seed.model_copy(
            update={
                "output_manifest_path": str(output_manifest),
                "case_count": len(cases),
                "success_count": success_count,
                "failure_count": failure_count,
                "backend_available": backend_available,
                "reasons": reasons,
            }
        )
        self.store.save_model(run_dir / "adapter-spec.json", adapter_spec)
        self.store.save_model(run_dir / "report.json", report)
        return report
