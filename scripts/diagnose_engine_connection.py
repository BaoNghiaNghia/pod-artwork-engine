from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from pod_artwork_engine.settings import Settings

BASE = "http://127.0.0.1:8765"
settings = Settings.from_env()
print("ENGINE_DIAGNOSTICS", settings.data_root)
for name in ("desktop-engine.log", "updater.log", "engine.log", "api.log"):
    path = settings.logs_dir / name
    print("LOG", name, "exists", path.is_file(), "bytes", path.stat().st_size if path.exists() else None)
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-28:]:
            print("   ", line[:400])

for route in ("/health", "/status"):
    for origin in (None, "http://tauri.localhost", "tauri://localhost", "http://localhost:1420"):
        headers = {"Origin": origin} if origin else {}
        req = urllib.request.Request(BASE + route, headers=headers)
        now = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=7) as response:
                raw = response.read()
                payload = json.loads(raw)
                print(
                    "PROBE", route, origin or "(no origin)", response.status,
                    "duration_s", round(time.monotonic() - now, 3),
                    "ACAO", response.headers.get("Access-Control-Allow-Origin"),
                    "keys", sorted(payload.keys())[:12],
                )
        except Exception as exc:
            print("PROBE_ERROR", route, origin or "(no origin)", round(time.monotonic() - now, 3), type(exc).__name__, str(exc))
