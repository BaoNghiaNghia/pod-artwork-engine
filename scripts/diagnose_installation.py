from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import psutil

from pod_artwork_engine.settings import Settings
from pod_artwork_engine.updater import UpdateManager
from pod_artwork_engine import __version__
from pod_artwork_engine.bootstrap import read_health


def short_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        h = hashlib.sha256()
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
        return h.hexdigest()[:16]


def main() -> None:
    settings = Settings.from_env()
    manager = UpdateManager(settings, __version__)
    home = Path(__file__).resolve().parents[1]
    active = manager.state()
    print("CODE VERSION", __version__)
    print("DATA ROOT", str(settings.data_root))
    print("UPDATER STATE", json.dumps({k: v for k, v in active.items()
          if k not in {"token", "secret", "password", "api_key"}}, ensure_ascii=False))
    print("HEALTH", read_health(settings))
    for base in (home / "build" / "release", *sorted(manager.releases_dir.glob("*"))):
        if not base.is_dir():
            continue
        print("RELEASE", str(base))
        for name in ("PODArtworkTool.exe", "pod-artwork-desktop.exe", "pod-artwork-engine.exe"):
            item = base / name
            print("  ", name, "exists", item.is_file(), "size", item.stat().st_size if item.is_file() else 0, "sha16", short_hash(item))
    for executable in ("PODArtworkTool.exe", "pod-artwork-desktop.exe", "pod-artwork-engine.exe"):
        for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                if (proc.info["name"] or "").lower() == executable.lower():
                    listeners = []
                    try:
                        for conn in proc.net_connections(kind="inet"):
                            if conn.status == psutil.CONN_LISTEN and conn.laddr:
                                listeners.append(conn.laddr.port)
                    except psutil.Error:
                        pass
                    print("PROCESS", {key: proc.info[key] for key in ("pid", "name", "exe", "cmdline")}, "parent_pid", proc.ppid(), "ports", listeners)
            except (OSError, psutil.Error):
                continue
    log = settings.logs_dir / "updater.log"
    if log.is_file():
        print("UPDATER LOG (last 40):")
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]:
            print(" ", line)


if __name__ == "__main__":
    main()
