from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from . import __version__
from .api import create_app
from .benchmark_suite import BenchmarkSuiteRunner
from .calibration import QCPolicyCalibrator
from .contracts import DatasetSplit, QualityMode, RouteKind
from .dataset_registry import DatasetRegistry
from .diagnostics import build_diagnostic_bundle
from .dewarp_benchmark import DewarpBenchmarkMatrixRunner, DewarpMaterializer
from .dewarp_experiment import DewarpExperimentRunner
from .hardware import detect_hardware
from .historical_import import HistoricalImporter
from .material_separation_benchmark import (
    MaterialSeparationBenchmarkMatrixRunner,
    MaterialSeparationMaterializer,
)
from .material_separation_experiment import MaterialSeparationExperimentRunner
from .policy_review import PolicyReviewKind, PolicyReviewPacketBuilder
from .harness import HarnessCaseFactory, HarnessRunner, HarnessStore, compare_scorecards, load_candidate_manifest, load_recipe
from .harness_engine import HarnessEngineRunner
from .harness_models import (
    BenchmarkSuiteSpec,
    BenchmarkTier,
    DewarpBenchmarkSpec,
    DewarpExperimentSpec,
    DewarpMaterializationSpec,
    MaterialSeparationBenchmarkSpec,
    MaterialSeparationExperimentSpec,
    MaterialSeparationMaterializationSpec,
    PromotionPolicy,
    RouteMatrixSpec,
    SRBenchmarkSpec,
    SRCohortSpec,
    SRExperimentSpec,
)
from .logging_config import LoggingRuntime
from .qc_policy import load_qc_policy
from .route_matrix import RouteMatrixRunner
from .registration_calibration import RegistrationPolicyCalibrator
from .router_calibration import RouterPolicyCalibrator
from .router_policy import load_router_policy
from .settings import Settings
from .sr_adapters import SRAdapterMaterializer, load_sr_adapter_spec
from .sr_cohort import SRCohortMaterializer
from .sr_experiment import SRExperimentRunner
from .sr_matrix import SRBenchmarkMatrixRunner
from .storage import StorageManager
from .updater import UpdateManager


def main() -> None:
    parser = argparse.ArgumentParser(prog="pod-artwork-engine")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve")
    sub.add_parser("status")
    sub.add_parser("hardware")
    sub.add_parser("cleanup")
    sub.add_parser("update-check")
    sub.add_parser("update-stage")
    sub.add_parser("dataset-list")

    diagnostics = sub.add_parser("diagnostics")
    diagnostics.add_argument("--output", type=Path)

    historical = sub.add_parser("historical-import")
    historical.add_argument("--source-dir", type=Path)
    historical.add_argument("--target-dir", type=Path)
    historical.add_argument("--manifest", type=Path)
    historical.add_argument("--dataset-name", default="historical")
    historical.add_argument("--id-regex")
    historical.add_argument("--seed", default="foundation-v1")
    historical.add_argument("--no-visual-fallback", action="store_true")

    dataset_show = sub.add_parser("dataset-show")
    dataset_show.add_argument("dataset_id")

    dataset_members = sub.add_parser("dataset-members")
    dataset_members.add_argument("dataset_id")
    dataset_members.add_argument(
        "--split",
        choices=[split.value for split in DatasetSplit],
    )
    dataset_members.add_argument("--retrieval-only", action="store_true")

    harness_cases = sub.add_parser("harness-cases")
    harness_cases.add_argument("dataset_id")
    harness_cases.add_argument("--tier", choices=[tier.value for tier in BenchmarkTier], required=True)
    harness_cases.add_argument("--limit", type=int)

    harness_run = sub.add_parser("harness-run")
    harness_run.add_argument("dataset_id")
    harness_run.add_argument("--tier", choices=[tier.value for tier in BenchmarkTier], required=True)
    harness_run.add_argument("--recipe", type=Path, required=True)
    harness_run.add_argument("--candidates", type=Path, required=True)
    harness_run.add_argument("--limit", type=int)

    harness_engine_run = sub.add_parser("harness-engine-run")
    harness_engine_run.add_argument("dataset_id")
    harness_engine_run.add_argument(
        "--tier",
        choices=[tier.value for tier in BenchmarkTier],
        required=True,
    )
    harness_engine_run.add_argument("--recipe", type=Path, required=True)
    harness_engine_run.add_argument(
        "--quality-mode",
        choices=[mode.value for mode in QualityMode],
        default=QualityMode.PRINT_READY.value,
    )
    harness_engine_run.add_argument("--limit", type=int)

    sub.add_parser("harness-scorecards")

    harness_compare = sub.add_parser("harness-compare")
    harness_compare.add_argument("champion_run_id")
    harness_compare.add_argument("challenger_run_id")
    harness_compare.add_argument("--allow-pre-golden", action="store_true")
    harness_compare.add_argument("--max-cohort-drop", type=float, default=0.03)

    harness_suite = sub.add_parser("harness-suite")
    harness_suite.add_argument("dataset_id")
    harness_suite.add_argument("--champion", type=Path, required=True)
    harness_suite.add_argument(
        "--challenger",
        type=Path,
        action="append",
        required=True,
    )
    harness_suite.add_argument(
        "--quality-mode",
        choices=[mode.value for mode in QualityMode],
        default=QualityMode.PRINT_READY.value,
    )
    harness_suite.add_argument("--smoke-limit", type=int, default=8)
    harness_suite.add_argument("--regression-limit", type=int)
    harness_suite.add_argument("--golden-limit", type=int)
    harness_suite.add_argument("--min-overall-delta", type=float, default=0.0)
    harness_suite.add_argument("--max-cohort-drop", type=float, default=0.03)
    harness_suite.add_argument("--max-latency-ratio", type=float)
    harness_suite.add_argument("--max-cost-ratio", type=float)

    harness_calibrate = sub.add_parser("harness-calibrate")
    harness_calibrate.add_argument("run_ids", nargs="+")
    harness_calibrate.add_argument("--allow-pre-golden", action="store_true")
    harness_calibrate.add_argument("--good-quality", type=float, default=0.85)
    harness_calibrate.add_argument("--bad-quality", type=float, default=0.70)
    harness_calibrate.add_argument("--min-good", type=int, default=5)
    harness_calibrate.add_argument("--min-bad", type=int, default=3)
    harness_calibrate.add_argument("--max-false-accept", type=float, default=0.05)
    harness_calibrate.add_argument("--max-threshold-delta", type=float, default=0.10)
    harness_calibrate.add_argument("--qc-policy", type=Path)

    route_matrix = sub.add_parser("harness-route-matrix")
    route_matrix.add_argument("dataset_id")
    route_matrix.add_argument(
        "--tier",
        choices=[tier.value for tier in BenchmarkTier],
        required=True,
    )
    route_matrix.add_argument("--recipe", type=Path, required=True)
    route_matrix.add_argument(
        "--quality-mode",
        choices=[mode.value for mode in QualityMode],
        default=QualityMode.PRINT_READY.value,
    )
    route_matrix.add_argument(
        "--route",
        choices=[route.value for route in RouteKind],
        action="append",
    )
    route_matrix.add_argument("--limit", type=int)
    route_matrix.add_argument("--min-quality-gain", type=float, default=0.02)

    sr_matrix = sub.add_parser("harness-sr-matrix")
    sr_matrix.add_argument("dataset_id")
    sr_matrix.add_argument(
        "--tier",
        choices=[tier.value for tier in BenchmarkTier],
        required=True,
    )
    sr_matrix.add_argument("--recipe", type=Path, required=True)
    sr_matrix.add_argument("--native-candidates", type=Path)
    sr_matrix.add_argument("--lanczos-candidates", type=Path)
    sr_matrix.add_argument("--local-sr-candidates", type=Path)
    sr_matrix.add_argument("--remote-sr-candidates", type=Path)
    sr_matrix.add_argument(
        "--quality-mode",
        choices=[mode.value for mode in QualityMode],
        default=QualityMode.PRINT_READY.value,
    )
    sr_matrix.add_argument("--limit", type=int)
    sr_matrix.add_argument("--min-quality-gain", type=float, default=0.01)
    sr_matrix.add_argument("--min-detail-gain", type=float, default=0.03)
    sr_matrix.add_argument("--max-semantic-drop", type=float, default=0.02)
    sr_matrix.add_argument("--max-latency-ratio", type=float)
    sr_matrix.add_argument("--max-cost-per-case-usd", type=float)

    sr_materialize = sub.add_parser("harness-sr-materialize")
    sr_materialize.add_argument("dataset_id")
    sr_materialize.add_argument(
        "--tier",
        choices=[tier.value for tier in BenchmarkTier],
        required=True,
    )
    sr_materialize.add_argument(
        "--input-candidates",
        type=Path,
        required=True,
    )
    sr_materialize.add_argument(
        "--adapter",
        type=Path,
        required=True,
    )
    sr_materialize.add_argument("--output-manifest", type=Path)
    sr_materialize.add_argument(
        "--quality-mode",
        choices=[mode.value for mode in QualityMode],
        default=QualityMode.PRINT_READY.value,
    )
    sr_materialize.add_argument("--limit", type=int)

    sr_cohort = sub.add_parser("harness-sr-cohort")
    sr_cohort.add_argument("dataset_id")
    sr_cohort.add_argument(
        "--tier",
        choices=[tier.value for tier in BenchmarkTier],
        required=True,
    )
    sr_cohort.add_argument(
        "--input-candidates",
        type=Path,
        required=True,
    )
    sr_cohort.add_argument("--scale-factor", type=float, default=2.0)
    sr_cohort.add_argument("--max-output-megapixels", type=float, default=80.0)
    sr_cohort.add_argument("--limit", type=int)

    sr_experiment = sub.add_parser("harness-sr-experiment")
    sr_experiment.add_argument("dataset_id")
    sr_experiment.add_argument("--pre-sr-candidates", type=Path, required=True)
    sr_experiment.add_argument("--recipe", type=Path, required=True)
    sr_experiment.add_argument("--local-adapter", type=Path)
    sr_experiment.add_argument("--remote-adapter", type=Path)
    sr_experiment.add_argument(
        "--tier",
        choices=[tier.value for tier in BenchmarkTier],
        default=BenchmarkTier.GOLDEN.value,
    )
    sr_experiment.add_argument("--allow-pre-golden", action="store_true")
    sr_experiment.add_argument(
        "--quality-mode",
        choices=[mode.value for mode in QualityMode],
        default=QualityMode.PRINT_READY.value,
    )
    sr_experiment.add_argument("--scale-factor", type=float, default=2.0)
    sr_experiment.add_argument("--max-output-megapixels", type=float, default=80.0)
    sr_experiment.add_argument("--limit", type=int)
    sr_experiment.add_argument("--min-quality-gain", type=float, default=0.01)
    sr_experiment.add_argument("--min-detail-gain", type=float, default=0.03)
    sr_experiment.add_argument("--max-semantic-drop", type=float, default=0.02)
    sr_experiment.add_argument("--max-latency-ratio", type=float)
    sr_experiment.add_argument("--max-cost-per-case-usd", type=float)
    sr_experiment.add_argument("--min-comparable-cases", type=int, default=3)
    sr_experiment.add_argument("--min-decisive-wins", type=int, default=2)
    sr_experiment.add_argument("--max-incomplete-rate", type=float, default=0.0)

    dewarp_materialize = sub.add_parser("harness-dewarp-materialize")
    dewarp_materialize.add_argument("dataset_id")
    dewarp_materialize.add_argument("--source-run-id", required=True)
    dewarp_materialize.add_argument("--registration-policy", type=Path, required=True)
    dewarp_materialize.add_argument("--canonical-size", type=int, default=512)
    dewarp_materialize.add_argument("--limit", type=int)

    dewarp_matrix = sub.add_parser("harness-dewarp-matrix")
    dewarp_matrix.add_argument("dataset_id")
    dewarp_matrix.add_argument("--recipe", type=Path, required=True)
    dewarp_matrix.add_argument("--native-candidates", type=Path, required=True)
    dewarp_matrix.add_argument("--dewarp-candidates", type=Path, required=True)
    dewarp_matrix.add_argument("--min-quality-gain", type=float, default=0.005)
    dewarp_matrix.add_argument("--min-technical-gain", type=float, default=0.0)
    dewarp_matrix.add_argument("--min-comparable-cases", type=int, default=3)
    dewarp_matrix.add_argument("--max-failure-rate-increase", type=float, default=0.0)
    dewarp_matrix.add_argument("--max-manual-review-rate-increase", type=float, default=0.0)
    dewarp_matrix.add_argument("--limit", type=int)

    dewarp_experiment = sub.add_parser("harness-dewarp-experiment")
    dewarp_experiment.add_argument("dataset_id")
    dewarp_experiment.add_argument("registration_run_ids", nargs="+")
    dewarp_experiment.add_argument("--recipe", type=Path, required=True)
    dewarp_experiment.add_argument("--materialization-source-run-id")
    dewarp_experiment.add_argument("--canonical-size", type=int, default=512)
    dewarp_experiment.add_argument("--limit", type=int)
    dewarp_experiment.add_argument("--calibration-min-cases-per-lane", type=int, default=3)
    dewarp_experiment.add_argument("--calibration-min-quality-score", type=float, default=0.80)
    dewarp_experiment.add_argument("--calibration-max-manual-review-rate", type=float, default=0.15)
    dewarp_experiment.add_argument("--min-comparable-cases", type=int, default=3)
    dewarp_experiment.add_argument("--min-quality-gain", type=float, default=0.005)
    dewarp_experiment.add_argument("--min-technical-gain", type=float, default=0.0)
    dewarp_experiment.add_argument("--min-small-detail-delta", type=float, default=0.0)
    dewarp_experiment.add_argument("--min-region-improvement-rate", type=float, default=0.60)
    dewarp_experiment.add_argument("--max-materialization-failure-rate", type=float, default=0.10)
    dewarp_experiment.add_argument("--max-failure-rate-increase", type=float, default=0.0)
    dewarp_experiment.add_argument("--max-manual-review-rate-increase", type=float, default=0.0)

    material_separation_materialize = sub.add_parser(
        "harness-material-separation-materialize"
    )
    material_separation_materialize.add_argument("dataset_id")
    material_separation_materialize.add_argument("--source-run-id", required=True)
    material_separation_materialize.add_argument(
        "--min-evidence-confidence",
        type=float,
        default=0.60,
    )
    material_separation_materialize.add_argument(
        "--color-distance-threshold",
        type=float,
        default=18.0,
    )
    material_separation_materialize.add_argument(
        "--color-distance-softness",
        type=float,
        default=24.0,
    )
    material_separation_materialize.add_argument("--limit", type=int)

    material_separation_matrix = sub.add_parser(
        "harness-material-separation-matrix"
    )
    material_separation_matrix.add_argument("dataset_id")
    material_separation_matrix.add_argument("--recipe", type=Path, required=True)
    material_separation_matrix.add_argument(
        "--native-candidates",
        type=Path,
        required=True,
    )
    material_separation_matrix.add_argument(
        "--separated-candidates",
        type=Path,
        required=True,
    )
    material_separation_matrix.add_argument(
        "--min-comparable-cases",
        type=int,
        default=3,
    )
    material_separation_matrix.add_argument(
        "--min-quality-gain",
        type=float,
        default=0.005,
    )
    material_separation_matrix.add_argument(
        "--min-alpha-gain",
        type=float,
        default=0.02,
    )
    material_separation_matrix.add_argument(
        "--min-technical-gain",
        type=float,
        default=0.0,
    )
    material_separation_matrix.add_argument(
        "--max-semantic-drop",
        type=float,
        default=0.01,
    )
    material_separation_matrix.add_argument(
        "--max-small-detail-drop",
        type=float,
        default=0.0,
    )
    material_separation_matrix.add_argument(
        "--max-halo-drop",
        type=float,
        default=0.0,
    )
    material_separation_matrix.add_argument(
        "--max-failure-rate-increase",
        type=float,
        default=0.0,
    )
    material_separation_matrix.add_argument(
        "--max-manual-review-rate-increase",
        type=float,
        default=0.0,
    )
    material_separation_matrix.add_argument("--limit", type=int)

    material_separation_experiment = sub.add_parser(
        "harness-material-separation-experiment"
    )
    material_separation_experiment.add_argument("dataset_id")
    material_separation_experiment.add_argument("--source-run-id", required=True)
    material_separation_experiment.add_argument("--recipe", type=Path, required=True)
    material_separation_experiment.add_argument(
        "--min-evidence-confidence",
        type=float,
        default=0.60,
    )
    material_separation_experiment.add_argument(
        "--color-distance-threshold",
        type=float,
        default=18.0,
    )
    material_separation_experiment.add_argument(
        "--color-distance-softness",
        type=float,
        default=24.0,
    )
    material_separation_experiment.add_argument(
        "--min-simple-cases",
        type=int,
        default=3,
    )
    material_separation_experiment.add_argument(
        "--min-comparable-cases",
        type=int,
        default=3,
    )
    material_separation_experiment.add_argument(
        "--min-quality-gain",
        type=float,
        default=0.005,
    )
    material_separation_experiment.add_argument(
        "--min-alpha-gain",
        type=float,
        default=0.02,
    )
    material_separation_experiment.add_argument(
        "--min-technical-gain",
        type=float,
        default=0.0,
    )
    material_separation_experiment.add_argument(
        "--max-semantic-drop",
        type=float,
        default=0.01,
    )
    material_separation_experiment.add_argument(
        "--max-small-detail-drop",
        type=float,
        default=0.0,
    )
    material_separation_experiment.add_argument(
        "--max-halo-drop",
        type=float,
        default=0.0,
    )
    material_separation_experiment.add_argument(
        "--max-materialization-failure-rate",
        type=float,
        default=0.10,
    )
    material_separation_experiment.add_argument(
        "--max-failure-rate-increase",
        type=float,
        default=0.0,
    )
    material_separation_experiment.add_argument(
        "--max-manual-review-rate-increase",
        type=float,
        default=0.0,
    )
    material_separation_experiment.add_argument("--limit", type=int)

    policy_review = sub.add_parser("harness-policy-review-packet")
    policy_review.add_argument(
        "--kind",
        choices=[kind.value for kind in PolicyReviewKind],
        required=True,
    )
    policy_review.add_argument("--proposal", type=Path, required=True)
    policy_review.add_argument("--packet-id")

    registration_calibrate = sub.add_parser("harness-registration-calibrate")
    registration_calibrate.add_argument("run_ids", nargs="+")
    registration_calibrate.add_argument("--min-cases-per-lane", type=int, default=3)
    registration_calibrate.add_argument("--min-quality-score", type=float, default=0.80)
    registration_calibrate.add_argument("--max-manual-review-rate", type=float, default=0.15)

    router_calibrate = sub.add_parser("harness-router-calibrate")
    router_calibrate.add_argument("matrix_ids", nargs="+")
    router_calibrate.add_argument("--allow-pre-golden", action="store_true")
    router_calibrate.add_argument("--min-remote", type=int, default=3)
    router_calibrate.add_argument("--min-deterministic", type=int, default=3)
    router_calibrate.add_argument("--max-false-local", type=float, default=0.05)
    router_calibrate.add_argument("--max-threshold-delta", type=float, default=0.15)
    router_calibrate.add_argument("--router-policy", type=Path)

    args = parser.parse_args()
    settings = Settings.from_env()
    settings.ensure_directories()

    if args.command == "serve":
        logging_runtime = LoggingRuntime(settings.logs_dir)
        logging_runtime.start()
        try:
            uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")
        finally:
            logging_runtime.stop()
    elif args.command == "status":
        print(json.dumps(StorageManager(settings).status().model_dump(mode="json"), indent=2))
    elif args.command == "hardware":
        print(json.dumps(detect_hardware().to_dict(), indent=2))
    elif args.command == "cleanup":
        print(json.dumps(StorageManager(settings).cleanup(), indent=2))
    elif args.command == "update-check":
        result = UpdateManager(settings, __version__).check()
        print(json.dumps(result.__dict__, indent=2))
    elif args.command == "update-stage":
        manager = UpdateManager(settings, __version__)
        manifest = manager.fetch_manifest()
        path = manager.stage(manifest)
        print(json.dumps({"version": manifest.version, "release_dir": str(path)}, indent=2))
    elif args.command == "diagnostics":
        print(build_diagnostic_bundle(settings, args.output))
    elif args.command == "historical-import":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        importer = HistoricalImporter(registry)
        if args.manifest:
            if args.source_dir or args.target_dir:
                parser.error("--manifest cannot be combined with --source-dir/--target-dir")
            report = importer.import_manifest(
                args.manifest,
                dataset_name=args.dataset_name,
                seed=args.seed,
            )
        else:
            if not args.source_dir or not args.target_dir:
                parser.error("historical-import requires --manifest or both --source-dir and --target-dir")
            report = importer.import_folders(
                args.source_dir,
                args.target_dir,
                dataset_name=args.dataset_name,
                id_regex=args.id_regex,
                seed=args.seed,
                allow_visual_fallback=not args.no_visual_fallback,
            )
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    elif args.command == "dataset-list":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        print(
            json.dumps(
                [dataset.model_dump(mode="json") for dataset in registry.list_datasets()],
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "dataset-show":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        dataset = registry.get_dataset(args.dataset_id)
        if dataset is None:
            parser.error(f"dataset not found: {args.dataset_id}")
        print(json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "dataset-members":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        dataset = registry.get_dataset(args.dataset_id)
        if dataset is None:
            parser.error(f"dataset not found: {args.dataset_id}")
        split = DatasetSplit(args.split) if args.split else None
        members = registry.list_members(
            args.dataset_id,
            split=split,
            retrieval_only=args.retrieval_only,
        )
        print(
            json.dumps(
                [member.model_dump(mode="json") for member in members],
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "harness-cases":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        cases = HarnessCaseFactory(registry).build(
            args.dataset_id,
            BenchmarkTier(args.tier),
            limit=args.limit,
        )
        print(
            json.dumps(
                [case.model_dump(mode="json") for case in cases],
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "harness-run":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        recipe = load_recipe(args.recipe.resolve())
        manifest_path = args.candidates.resolve()
        manifest = load_candidate_manifest(manifest_path)
        scorecard = HarnessRunner(registry, store).run_candidates(
            args.dataset_id,
            BenchmarkTier(args.tier),
            recipe,
            manifest,
            manifest_base=manifest_path.parent,
            limit=args.limit,
        )
        print(json.dumps(scorecard.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-engine-run":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        recipe_path = args.recipe.resolve()
        recipe = load_recipe(recipe_path)
        scorecard = HarnessEngineRunner(settings, registry, store).run(
            args.dataset_id,
            BenchmarkTier(args.tier),
            recipe,
            quality_mode=QualityMode(args.quality_mode),
            limit=args.limit,
            recipe_base=recipe_path.parent,
        )
        print(json.dumps(scorecard.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-scorecards":
        store = HarnessStore(settings.harness_dir)
        print(
            json.dumps(
                [item.model_dump(mode="json") for item in store.list_scorecards()],
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "harness-compare":
        store = HarnessStore(settings.harness_dir)
        champion = store.get_scorecard(args.champion_run_id)
        challenger = store.get_scorecard(args.challenger_run_id)
        if champion is None:
            parser.error(f"scorecard not found: {args.champion_run_id}")
        if challenger is None:
            parser.error(f"scorecard not found: {args.challenger_run_id}")
        decision = compare_scorecards(
            champion,
            challenger,
            PromotionPolicy(
                max_cohort_drop=args.max_cohort_drop,
                require_golden=not args.allow_pre_golden,
            ),
        )
        print(json.dumps(decision.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-suite":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        spec = BenchmarkSuiteSpec(
            dataset_id=args.dataset_id,
            champion_recipe_path=str(args.champion),
            challenger_recipe_paths=[str(path) for path in args.challenger],
            quality_mode=QualityMode(args.quality_mode),
            smoke_limit=args.smoke_limit,
            regression_limit=args.regression_limit,
            golden_limit=args.golden_limit,
            promotion_policy=PromotionPolicy(
                min_overall_delta=args.min_overall_delta,
                max_cohort_drop=args.max_cohort_drop,
                max_latency_ratio=args.max_latency_ratio,
                max_cost_ratio=args.max_cost_ratio,
                require_golden=True,
            ),
        )
        report = BenchmarkSuiteRunner(settings, registry, store).run(
            spec,
            spec_base=Path.cwd(),
        )
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-calibrate":
        store = HarnessStore(settings.harness_dir)
        policy_path = (
            args.qc_policy.resolve()
            if args.qc_policy is not None
            else settings.qc_policy_path
        )
        current_policy = load_qc_policy(policy_path)
        proposal = QCPolicyCalibrator(store, current_policy).propose(
            args.run_ids,
            require_golden=not args.allow_pre_golden,
            good_quality_threshold=args.good_quality,
            bad_quality_threshold=args.bad_quality,
            min_good=args.min_good,
            min_bad=args.min_bad,
            max_false_accept_rate=args.max_false_accept,
            max_threshold_delta=args.max_threshold_delta,
        )
        print(json.dumps(proposal.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-route-matrix":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        routes = (
            [RouteKind(value) for value in args.route]
            if args.route
            else [RouteKind.DETERMINISTIC, RouteKind.HYBRID]
        )
        spec = RouteMatrixSpec(
            dataset_id=args.dataset_id,
            tier=BenchmarkTier(args.tier),
            recipe_path=str(args.recipe),
            quality_mode=QualityMode(args.quality_mode),
            routes=routes,
            limit=args.limit,
            min_quality_gain=args.min_quality_gain,
        )
        report = RouteMatrixRunner(settings, registry, store).run(
            spec,
            spec_base=Path.cwd(),
        )
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-sr-matrix":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        spec = SRBenchmarkSpec(
            dataset_id=args.dataset_id,
            tier=BenchmarkTier(args.tier),
            recipe_path=str(args.recipe),
            native_manifest_path=(
                str(args.native_candidates)
                if args.native_candidates is not None
                else None
            ),
            lanczos_manifest_path=(
                str(args.lanczos_candidates)
                if args.lanczos_candidates is not None
                else None
            ),
            local_sr_manifest_path=(
                str(args.local_sr_candidates)
                if args.local_sr_candidates is not None
                else None
            ),
            remote_sr_manifest_path=(
                str(args.remote_sr_candidates)
                if args.remote_sr_candidates is not None
                else None
            ),
            quality_mode=QualityMode(args.quality_mode),
            limit=args.limit,
            min_quality_gain=args.min_quality_gain,
            min_detail_gain=args.min_detail_gain,
            max_semantic_drop=args.max_semantic_drop,
            max_latency_ratio=args.max_latency_ratio,
            max_cost_per_case_usd=args.max_cost_per_case_usd,
        )
        report = SRBenchmarkMatrixRunner(settings, registry, store).run(
            spec,
            spec_base=Path.cwd(),
        )
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-sr-materialize":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        adapter_spec = load_sr_adapter_spec(args.adapter.resolve())
        report = SRAdapterMaterializer(
            settings,
            registry,
            store,
        ).materialize(
            dataset_id=args.dataset_id,
            tier=BenchmarkTier(args.tier),
            source_manifest_path=args.input_candidates.resolve(),
            adapter_spec=adapter_spec,
            quality_mode=QualityMode(args.quality_mode),
            limit=args.limit,
            output_manifest_path=(
                args.output_manifest.resolve()
                if args.output_manifest is not None
                else None
            ),
        )
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-sr-cohort":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        spec = SRCohortSpec(
            dataset_id=args.dataset_id,
            tier=BenchmarkTier(args.tier),
            input_manifest_path=str(args.input_candidates.resolve()),
            scale_factor=args.scale_factor,
            max_output_megapixels=args.max_output_megapixels,
            limit=args.limit,
        )
        report = SRCohortMaterializer(
            settings,
            registry,
            store,
        ).materialize(spec)
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-sr-experiment":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        spec = SRExperimentSpec(
            dataset_id=args.dataset_id,
            pre_sr_manifest_path=str(args.pre_sr_candidates),
            recipe_path=str(args.recipe),
            tier=BenchmarkTier(args.tier),
            require_golden=not args.allow_pre_golden,
            local_adapter_path=(
                str(args.local_adapter) if args.local_adapter is not None else None
            ),
            remote_adapter_path=(
                str(args.remote_adapter) if args.remote_adapter is not None else None
            ),
            quality_mode=QualityMode(args.quality_mode),
            scale_factor=args.scale_factor,
            max_output_megapixels=args.max_output_megapixels,
            limit=args.limit,
            min_quality_gain=args.min_quality_gain,
            min_detail_gain=args.min_detail_gain,
            max_semantic_drop=args.max_semantic_drop,
            max_latency_ratio=args.max_latency_ratio,
            max_cost_per_case_usd=args.max_cost_per_case_usd,
            min_comparable_cases=args.min_comparable_cases,
            min_decisive_wins=args.min_decisive_wins,
            max_incomplete_rate=args.max_incomplete_rate,
        )
        report = SRExperimentRunner(settings, registry, store).run(
            spec,
            spec_base=Path.cwd(),
        )
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-dewarp-materialize":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        spec = DewarpMaterializationSpec(
            dataset_id=args.dataset_id,
            source_run_id=args.source_run_id,
            registration_policy_path=str(args.registration_policy.resolve()),
            canonical_size=args.canonical_size,
            limit=args.limit,
        )
        report = DewarpMaterializer(settings, registry, store).materialize(spec)
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-dewarp-matrix":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        spec = DewarpBenchmarkSpec(
            dataset_id=args.dataset_id,
            recipe_path=str(args.recipe.resolve()),
            native_manifest_path=str(args.native_candidates.resolve()),
            dewarp_manifest_path=str(args.dewarp_candidates.resolve()),
            min_quality_gain=args.min_quality_gain,
            min_technical_gain=args.min_technical_gain,
            min_comparable_cases=args.min_comparable_cases,
            max_failure_rate_increase=args.max_failure_rate_increase,
            max_manual_review_rate_increase=args.max_manual_review_rate_increase,
            limit=args.limit,
        )
        report = DewarpBenchmarkMatrixRunner(settings, registry, store).run(
            spec,
            spec_base=Path.cwd(),
        )
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-dewarp-experiment":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        spec = DewarpExperimentSpec(
            dataset_id=args.dataset_id,
            registration_run_ids=list(args.registration_run_ids),
            recipe_path=str(args.recipe.resolve()),
            materialization_source_run_id=args.materialization_source_run_id,
            canonical_size=args.canonical_size,
            limit=args.limit,
            calibration_min_cases_per_lane=args.calibration_min_cases_per_lane,
            calibration_min_quality_score=args.calibration_min_quality_score,
            calibration_max_manual_review_rate=args.calibration_max_manual_review_rate,
            min_comparable_cases=args.min_comparable_cases,
            min_quality_gain=args.min_quality_gain,
            min_technical_gain=args.min_technical_gain,
            min_small_detail_delta=args.min_small_detail_delta,
            min_region_improvement_rate=args.min_region_improvement_rate,
            max_materialization_failure_rate=args.max_materialization_failure_rate,
            max_failure_rate_increase=args.max_failure_rate_increase,
            max_manual_review_rate_increase=args.max_manual_review_rate_increase,
        )
        report = DewarpExperimentRunner(settings, registry, store).run(
            spec,
            spec_base=Path.cwd(),
        )
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-material-separation-materialize":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        spec = MaterialSeparationMaterializationSpec(
            dataset_id=args.dataset_id,
            source_run_id=args.source_run_id,
            min_evidence_confidence=args.min_evidence_confidence,
            color_distance_threshold=args.color_distance_threshold,
            color_distance_softness=args.color_distance_softness,
            limit=args.limit,
        )
        report = MaterialSeparationMaterializer(
            settings,
            registry,
            store,
        ).materialize(spec)
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-material-separation-matrix":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        spec = MaterialSeparationBenchmarkSpec(
            dataset_id=args.dataset_id,
            recipe_path=str(args.recipe.resolve()),
            native_manifest_path=str(args.native_candidates.resolve()),
            separated_manifest_path=str(args.separated_candidates.resolve()),
            min_comparable_cases=args.min_comparable_cases,
            min_quality_gain=args.min_quality_gain,
            min_alpha_gain=args.min_alpha_gain,
            min_technical_gain=args.min_technical_gain,
            max_semantic_drop=args.max_semantic_drop,
            max_small_detail_drop=args.max_small_detail_drop,
            max_halo_drop=args.max_halo_drop,
            max_failure_rate_increase=args.max_failure_rate_increase,
            max_manual_review_rate_increase=args.max_manual_review_rate_increase,
            limit=args.limit,
        )
        report = MaterialSeparationBenchmarkMatrixRunner(
            settings,
            registry,
            store,
        ).run(spec, spec_base=Path.cwd())
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-material-separation-experiment":
        registry = DatasetRegistry(settings.database_path, settings.datasets_dir)
        store = HarnessStore(settings.harness_dir)
        spec = MaterialSeparationExperimentSpec(
            dataset_id=args.dataset_id,
            source_run_id=args.source_run_id,
            recipe_path=str(args.recipe.resolve()),
            min_evidence_confidence=args.min_evidence_confidence,
            color_distance_threshold=args.color_distance_threshold,
            color_distance_softness=args.color_distance_softness,
            min_simple_cases=args.min_simple_cases,
            min_comparable_cases=args.min_comparable_cases,
            min_quality_gain=args.min_quality_gain,
            min_alpha_gain=args.min_alpha_gain,
            min_technical_gain=args.min_technical_gain,
            max_semantic_drop=args.max_semantic_drop,
            max_small_detail_drop=args.max_small_detail_drop,
            max_halo_drop=args.max_halo_drop,
            max_materialization_failure_rate=args.max_materialization_failure_rate,
            max_failure_rate_increase=args.max_failure_rate_increase,
            max_manual_review_rate_increase=args.max_manual_review_rate_increase,
            limit=args.limit,
        )
        report = MaterialSeparationExperimentRunner(
            settings,
            registry,
            store,
        ).run(spec, spec_base=Path.cwd())
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-policy-review-packet":
        store = HarnessStore(settings.harness_dir)
        packet = PolicyReviewPacketBuilder(store).build(
            PolicyReviewKind(args.kind),
            args.proposal.resolve(),
            packet_id=args.packet_id,
        )
        print(json.dumps(packet.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-registration-calibrate":
        store = HarnessStore(settings.harness_dir)
        proposal = RegistrationPolicyCalibrator(store).propose(
            args.run_ids,
            min_cases_per_lane=args.min_cases_per_lane,
            min_quality_score=args.min_quality_score,
            max_manual_review_rate=args.max_manual_review_rate,
        )
        print(json.dumps(proposal.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.command == "harness-router-calibrate":
        store = HarnessStore(settings.harness_dir)
        policy_path = (
            args.router_policy.resolve()
            if args.router_policy is not None
            else settings.router_policy_path
        )
        current_policy = load_router_policy(policy_path)
        proposal = RouterPolicyCalibrator(store, current_policy).propose(
            args.matrix_ids,
            require_golden=not args.allow_pre_golden,
            min_remote=args.min_remote,
            min_deterministic=args.min_deterministic,
            max_false_local_rate=args.max_false_local,
            max_threshold_delta=args.max_threshold_delta,
        )
        print(json.dumps(proposal.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
