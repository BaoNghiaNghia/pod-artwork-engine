"""Smoke-test the packaged engine's Tauri/WebView2 CORS response on an isolated port."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil

from pod_artwork_engine import __version__


def probe(base_url: str, path: str, origin: str, *, method: str = "GET") -> tuple[int, str | None, dict]:
    headers = {"Origin": origin}
    if method == "OPTIONS":
        headers["Access-Control-Request-Method"] = "POST"
        headers["Access-Control-Request-Headers"] = "content-type"
    request = urllib.request.Request(base_url + path, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read()) if method != "OPTIONS" else {}
            return response.status, response.headers.get("Access-Control-Allow-Origin"), payload
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Access-Control-Allow-Origin"), {}


def main() -> None:
    engine = Path(__file__).resolve().parents[1] / "build" / "release" / "pod-artwork-engine.exe"
    if not engine.is_file():
        raise SystemExit(f"missing packaged engine: {engine}")
    with tempfile.TemporaryDirectory(prefix="pod-cors-smoke-") as temporary:
        path = Path(temporary)
        log_path = path / "engine-stderr.log"
        env = os.environ.copy()
        env["POD_ENGINE_PORT"] = "18765"
        env["POD_ENGINE_HOST"] = "127.0.0.1"
        env["POD_ARTWORK_DATA"] = str(path / "data")
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [str(engine), "serve"],
                cwd=str(engine.parent),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                base = "http://127.0.0.1:18765"
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"engine exited early with {process.returncode}")
                    try:
                        _, _, health = probe(base, "/health", "http://tauri.localhost")
                        if health.get("status") == "ok":
                            break
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.25)
                else:
                    raise RuntimeError("engine did not become healthy")

                checks = (
                    ("/health", "http://tauri.localhost", "GET", 200, True),
                    ("/status", "http://tauri.localhost", "GET", 200, True),
                    ("/jobs?quality_mode=print_ready", "http://tauri.localhost", "OPTIONS", 200, True),
                    ("/historical/onboarding/preflight", "http://tauri.localhost", "OPTIONS", 200, True),
                    ("/status", "https://malicious.example", "GET", 200, False),
                )
                for route, origin, method, expected_status, allowed in checks:
                    status, response_origin, payload = probe(base, route, origin, method=method)
                    if status != expected_status or (response_origin == origin) != allowed:
                        raise AssertionError(
                            f"{route} {method} origin={origin} returned "
                            f"status={status} ACAO={response_origin}"
                        )
                    if route == "/status" and allowed and payload.get("version") != __version__:
                        raise AssertionError(f"unexpected engine version {payload.get('version')}")
                    print(f"PASS {method} {route} origin={origin}: HTTP {status}, ACAO={response_origin}")
            except Exception:
                log.flush()
                if log_path.is_file():
                    print(log_path.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)
                raise
            finally:
                try:
                    parent = psutil.Process(process.pid)
                    children = parent.children(recursive=True)
                    for item in reversed(children):
                        item.terminate()
                    parent.terminate()
                    psutil.wait_procs([*children, parent], timeout=3)
                except psutil.NoSuchProcess:
                    pass


if __name__ == "__main__":
    main()
