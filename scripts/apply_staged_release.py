"""Safely activate a staged POD release after checking persisted jobs.

Targets *only* managed POD desktop and engine processes under
%LOCALAPPDATA%/PODArtworkTool/updates/releases. Refuses to interrupt any
active/queued job. No force-kill, no unrelated process termination.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil

from pod_artwork_engine import __version__
from pod_artwork_engine.settings import Settings
from pod_artwork_engine.updater import UpdateManager

INACTIVE = {"completed", "review_required", "failed_final", "cancelled", "blocked_budget"}
NAMES = {"pod-artwork-desktop.exe", "pod-artwork-engine.exe"}


def main() -> int:
    settings = Settings.from_env()
    manager = UpdateManager(settings, __version__)
    staged = manager.staged_release()
    if staged is None:
        print("No verified staged release; nothing to activate.")
        return 2
    expected_version, _ = staged
    host = "127.0.0.1" if settings.host in {"0.0.0.0", "::"} else settings.host
    jobs_url = f"http://{host}:{settings.port}/jobs"
    try:
        with urllib.request.urlopen(jobs_url, timeout=5) as response:
            jobs = json.loads(response.read(5 * 1024 * 1024))
    except (OSError, ValueError) as error:
        print("Unable to verify job states; refusing process shutdown:", type(error).__name__)
        return 3
    active = [j.get("job_id") for j in jobs if j.get("state") not in INACTIVE]
    if active:
        print(f"Refusing deployment: {len(active)} jobs still active/queued")
        return 4

    managed_root = (settings.updates_dir / "releases").resolve()
    targets = []
    for p in psutil.process_iter(["name", "exe"]):
        try:
            name = str(p.info.get("name") or "").lower()
            exe = p.info.get("exe")
            if name not in NAMES or not exe:
                continue
            path = Path(exe).resolve()
            if path.parent.parent == managed_root:
                targets.append(p)
        except (psutil.Error, OSError, ValueError):
            continue
    print(f"Active/queued jobs: 0; terminating {len(targets)} owned POD processes")
    # Close desktop before Engine; never touch unrelated processes.
    targets.sort(key=lambda p: 0 if p.info.get("name", "").lower() == "pod-artwork-desktop.exe" else 1)
    for process in targets:
        try:
            process.terminate()
            process.wait(timeout=12)
            print(f"Stopped managed {process.info.get('name')} PID={process.pid}")
        except psutil.NoSuchProcess:
            pass
        except (psutil.AccessDenied, psutil.TimeoutExpired) as exc:
            print(f"Unable to stop PID={process.pid} ({type(exc).__name__}); aborting")
            return 5

    launcher = Path(__file__).resolve().parents[1] / "build" / "release" / "PODArtworkTool.exe"
    if not launcher.is_file():
        print("Packaged launcher missing:", launcher)
        return 6
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen([str(launcher)], cwd=str(launcher.parent), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    check_url = f"http://{host}:{settings.port}/health"
    for _ in range(60):
        time.sleep(1)
        try:
            with urllib.request.urlopen(check_url, timeout=2) as response:
                health = json.loads(response.read())
            if health.get("status") == "ok" and health.get("version") == expected_version:
                current = UpdateManager(settings, expected_version).active_version()
                if current == expected_version:
                    print(f"Activated and verified POD Artwork Tool v{expected_version}")
                    return 0
        except (OSError, ValueError):
            pass
    print("Activation health check timed out; consult updater.log")
    return 7


if __name__ == "__main__":
    sys.exit(main())
