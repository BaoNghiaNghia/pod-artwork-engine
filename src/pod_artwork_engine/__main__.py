from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from .api import create_app
from .diagnostics import build_diagnostic_bundle
from .logging_config import LoggingRuntime
from .settings import Settings
from .storage import StorageManager


def main() -> None:
    parser = argparse.ArgumentParser(prog="pod-artwork-engine")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve")
    sub.add_parser("status")
    sub.add_parser("cleanup")

    diagnostics = sub.add_parser("diagnostics")
    diagnostics.add_argument("--output", type=Path)

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
    elif args.command == "cleanup":
        print(json.dumps(StorageManager(settings).cleanup(), indent=2))
    elif args.command == "diagnostics":
        print(build_diagnostic_bundle(settings, args.output))


if __name__ == "__main__":
    main()
