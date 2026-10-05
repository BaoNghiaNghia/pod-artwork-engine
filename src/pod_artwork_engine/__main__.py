from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from . import __version__
from .api import create_app
from .contracts import DatasetSplit
from .dataset_registry import DatasetRegistry
from .diagnostics import build_diagnostic_bundle
from .hardware import detect_hardware
from .historical_import import HistoricalImporter
from .harness import HarnessCaseFactory, HarnessRunner, HarnessStore, compare_scorecards, load_candidate_manifest, load_recipe
from .harness_models import BenchmarkTier, PromotionPolicy
from .logging_config import LoggingRuntime
from .settings import Settings
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

    sub.add_parser("harness-scorecards")

    harness_compare = sub.add_parser("harness-compare")
    harness_compare.add_argument("champion_run_id")
    harness_compare.add_argument("challenger_run_id")
    harness_compare.add_argument("--allow-pre-golden", action="store_true")
    harness_compare.add_argument("--max-cohort-drop", type=float, default=0.03)

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


if __name__ == "__main__":
    main()
