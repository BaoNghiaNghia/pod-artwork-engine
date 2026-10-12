# POD Artwork Tool v0.1.11 — Logger & Auto Update

## Runtime logging
- **Engine**: `%LOCALAPPDATA%\\PODArtworkTool\\logs\\engine.log`, JSON lines with UTC timestamp, level, logger, event, safe route template and processing duration. Engine rotation: 25 MB per file, five backups.
- **Updater**: `updater.log`, launcher events covering manifest check, download, health validation, promotion and rollback. Rotation: 5 MB and three backups.
- **Desktop Engine startup stderr**: `desktop-engine.log` (5 MB + one backup) plus startup failure context.
- **Per-job**: `logs/jobs/<job_id>.jsonl`, maximum ~2 MB per active log file with two backups, 14-day retention at Engine startup. Unsafe job IDs cannot create arbitrary files.
- **Application actions**: allowlisted UI navigation and mutations are logged as events. API POST mutations log route templates, result HTTP code and duration, not uploaded file names, request bodies, prompts or API keys.

UI: `AI Settings -> Version & updates / Logger`. Log UI displays rotation policy, log file metadata, safe recent events, local log folder, and event count. Arbitrary raw log text and exceptions are intentionally not exposed through the API.

## Version checks and install
- Bootstrapper checks the stable manifest at application startup. Checksum-verifies ZIP, extracts safely and validates expected engine + desktop files.
- While desktop is open, use **AI Settings -> Check for updates**. A one-time background version check runs when Engine becomes ready, and the same UI permits a manual check.
- **Download update** prepares a new version and shows **restart required**. It **does not close the app or running jobs**. Close and relaunch the tool using the launcher to perform health-gated activation.
- The launcher compares version and instance token on local /health. On failure, it discards the staged version and falls back to the current release, with previous version rollback support.
- `update-state.json` tracks `current_version`, `previous_version`, `staged_version`, timestamps and errors; do not edit it manually.
- If disconnected, the tool continues using the existing version and presents a recoverable error. Manual checks use a lock to prevent duplicate downloads.
- Maintainer deployment when a desktop or Engine process is stuck: run `.venv\\Scripts\\python.exe scripts/inspect_pod_processes.py` to identify *only POD* processes, then `.venv\\Scripts\\python.exe scripts/apply_staged_release.py`. The latter refuses to proceed if any job is active/queued or when no validated staged release exists. It only stops managed POD processes under the updates/release directory, starts the packaged bootstrap, and verifies the new `/health` version and updater state. Do not use while editing unsaved inputs.

## Verification
- `npm --prefix desktop run build`
- `npm --prefix desktop run qa:ui` (requires running Vite dev server)
- `.venv\\Scripts\\python.exe -m pytest tests/test_system_monitor.py tests/test_logging.py tests/test_updater.py tests/test_bootstrap.py -q`
- On a packaged Windows release, confirm `/health` reports the new version and `/system/overview` reports the current update state.

Security: Update fetches only the configured release manifest, and downloads are SHA-256 validated. The local Engine is bound to loopback with a configured CORS allowlist. Do not share raw `logs` or diagnostic archives without reviewing them for sensitive data.

## v0.1.12: In-app support diagnostics & long-session update checks
- **AI Settings → Logger** displays recent event counts and failures from the last 24 hours (a bounded sample of up to 100 safe events; this is not an exhaustive 24-hour total).
- **Export diagnostic JSON** downloads an in-memory support snapshot containing only release versions, updater status timestamps/flags, log sizes, log retention, and aggregate event/error counts. No raw logs, exception messages, filenames, local paths, AI prompts, provider response bodies, or credentials are included.
- The active desktop checks the stable release every 6 hours while Engine remains online, as well as on startup and on reconnection. Manual **Check for updates** remains available. New versions are never applied during running jobs; activation remains a separate launcher restart.
- The safe-event viewer validates event identifiers and rejects malformed or unbounded fields before showing them. This reduces accidental data disclosure from unexpected log records.
- Quality gates: `tests/test_safe_diagnostics.py`, `tests/test_system_monitor.py`, `npm --prefix desktop run qa:ui` and all prior updater/rollback tests.
