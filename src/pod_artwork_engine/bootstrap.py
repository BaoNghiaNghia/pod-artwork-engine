from __future__ import annotations

import json
import logging
import logging.handlers
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .settings import Settings
from .updater import DESKTOP_EXECUTABLE, UpdateManager


logger = logging.getLogger("pod_bootstrap")


@dataclass(frozen=True)
class HealthResult:
    healthy: bool
    version: str | None
    reason: str
    instance_token: str | None = None


def bootstrap_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def configure_bootstrap_logging(settings: Settings) -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    if logger.handlers:
        return
    handler = logging.handlers.RotatingFileHandler(
        settings.logs_dir / "updater.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False


def health_url(settings: Settings) -> str:
    host = settings.host
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{settings.port}/health"


def read_health(
    settings: Settings,
    timeout_seconds: float = 1.0,
    expected_token: str | None = None,
) -> HealthResult:
    try:
        with urllib.request.urlopen(health_url(settings), timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        version = str(payload.get("version")) if payload.get("version") is not None else None
        instance_token = payload.get("instance_token")
        if payload.get("status") != "ok":
            return HealthResult(False, version, "engine status is not ok", instance_token)
        if expected_token is not None and instance_token != expected_token:
            return HealthResult(
                False,
                version,
                "engine instance token does not match launched release",
                instance_token,
            )
        return HealthResult(True, version, "ok", instance_token)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        return HealthResult(False, None, str(exc))


def wait_for_release_health(
    process: subprocess.Popen,
    settings: Settings,
    expected_version: str,
    expected_token: str,
    timeout_seconds: float = 20.0,
) -> HealthResult:
    deadline = time.monotonic() + timeout_seconds
    last = HealthResult(False, None, "health check not started")
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            return HealthResult(False, None, f"desktop exited early with code {return_code}")

        last = read_health(settings, expected_token=expected_token)
        if last.healthy and last.version == expected_version:
            return last
        if last.healthy and last.version != expected_version:
            last = HealthResult(
                False,
                last.version,
                f"engine version mismatch: expected {expected_version}, got {last.version}",
                last.instance_token,
            )
        time.sleep(0.35)
    return HealthResult(False, last.version, f"health timeout: {last.reason}", last.instance_token)


def launch_desktop(release_dir: Path, instance_token: str) -> subprocess.Popen:
    executable = release_dir / DESKTOP_EXECUTABLE
    if not executable.is_file():
        raise FileNotFoundError(executable)
    child_env = os.environ.copy()
    child_env["POD_BOOTSTRAP_TOKEN"] = instance_token
    kwargs: dict = {
        "cwd": str(release_dir),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": child_env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen([str(executable)], **kwargs)


def terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def _launch_and_validate(
    release_dir: Path,
    version: str,
    settings: Settings,
    timeout_seconds: float,
) -> tuple[subprocess.Popen | None, HealthResult]:
    instance_token = secrets.token_hex(16)
    try:
        process = launch_desktop(release_dir, instance_token)
    except OSError as exc:
        return None, HealthResult(False, None, f"failed to launch desktop: {exc}")

    result = wait_for_release_health(
        process,
        settings,
        version,
        instance_token,
        timeout_seconds,
    )
    if not result.healthy:
        terminate_process_tree(process)
    return process, result


def run_bootstrap(
    settings: Settings | None = None,
    *,
    install_dir: Path | None = None,
    current_version: str = __version__,
    health_timeout_seconds: float = 20.0,
) -> int:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    configure_bootstrap_logging(settings)
    install_dir = (install_dir or bootstrap_directory()).resolve()
    manager = UpdateManager(settings, current_version)

    try:
        current_dir = manager.seed_current_release(install_dir, current_version)
        logger.info("seed/current release ready version=%s path=%s", manager.active_version(), current_dir)
    except Exception as exc:
        current_dir = manager.current_release_dir()
        if current_dir is None:
            logger.exception("no valid installed release is available")
            return 20
        logger.warning("could not seed bundled release; using managed current release: %s", exc)

    if settings.release_manifest_url:
        try:
            check = manager.check()
            logger.info(
                "update check current=%s latest=%s available=%s reason=%s",
                check.current_version,
                check.latest_version,
                check.update_available,
                check.reason,
            )
            if check.update_available:
                manifest = manager.fetch_manifest()
                manager.stage(manifest)
                logger.info("staged release version=%s", manifest.version)
        except Exception as exc:
            manager.mark_failed(f"update check/stage failed: {exc}")
            logger.exception("update check/stage failed; continuing with installed release")

    staged = manager.staged_release()
    if staged is not None:
        staged_version, staged_dir = staged
        logger.info("validating staged release version=%s", staged_version)
        process, health = _launch_and_validate(
            staged_dir,
            staged_version,
            settings,
            health_timeout_seconds,
        )
        if process is not None and health.healthy:
            manager.mark_active(staged_version)
            logger.info("activated release version=%s", staged_version)
            return 0

        error = f"staged release {staged_version} failed health check: {health.reason}"
        manager.discard_staged(error)
        logger.error(error)

    active_version = manager.active_version()
    current_dir = manager.current_release_dir()
    if current_dir is not None:
        logger.info("launching active release version=%s", active_version)
        process, health = _launch_and_validate(
            current_dir,
            active_version,
            settings,
            health_timeout_seconds,
        )
        if process is not None and health.healthy:
            logger.info("active release healthy version=%s", active_version)
            return 0

        active_error = f"active release {active_version} failed health check: {health.reason}"
        manager.mark_failed(active_error, failed_version=active_version)
        logger.error(active_error)
    else:
        active_error = f"active release {active_version} is missing or invalid"
        manager.mark_failed(active_error, failed_version=active_version)
        logger.error(active_error)

    rollback = manager.rollback_target()
    if rollback is None:
        logger.error("automatic rollback unavailable")
        return 21

    rollback_version, rollback_dir = rollback
    logger.warning(
        "attempting automatic rollback failed=%s target=%s",
        active_version,
        rollback_version,
    )
    process, health = _launch_and_validate(
        rollback_dir,
        rollback_version,
        settings,
        health_timeout_seconds,
    )
    if process is None or not health.healthy:
        rollback_error = (
            f"rollback release {rollback_version} failed health check: {health.reason}"
        )
        manager.mark_failed(rollback_error, failed_version=active_version)
        logger.error(rollback_error)
        return 22

    manager.mark_rolled_back(active_version, rollback_version, active_error)
    logger.warning("rollback successful active=%s", rollback_version)
    return 0


def main() -> None:
    raise SystemExit(run_bootstrap())


if __name__ == "__main__":
    main()
