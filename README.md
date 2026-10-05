# POD Artwork Engine

Standalone Windows tool for reconstructing clean 2D POD artwork from product/mockup/reference images.

## V1 hardware profile

- Intel Xeon E5-2680 v4, 28 cores / 56 logical processors
- 64 GB RAM
- Radeon RX 470 8 GB
- SSD
- Remote-first semantic reconstruction
- CPU-first local precision processing
- 40 GB absolute tool-storage cap

## Current implementation stage

### Phase 0A — Standalone Foundation

The repository currently establishes:

- Tauri desktop shell plus an independently packaged engine sidecar;
- versioned typed contracts;
- persistent SQLite job state;
- atomic checkpoints and restart recovery;
- structured non-blocking global and per-job logging;
- dynamic CPU/RAM/GPU profiling and resource defaults;
- 32 GB storage soft limit / 40 GB hard limit with cleanup policies;
- redacted diagnostic bundles;
- release-manifest, SHA-256 verification, safe update staging and rollback metadata;
- local engine API;
- Windows startup/build scripts;
- a verified Windows release executable that automatically starts the engine sidecar.

Phase 0A standalone foundation is now closed for the local runtime: the independent `PODArtworkTool.exe` bootstrap seeds the current release, checks/stages updates, launches the candidate release, verifies the engine version and per-launch instance token through `/health`, activates only on success, and automatically rolls back to the previous healthy release on failure. Release-manifest hosting/signing policy remains a deployment concern rather than a runtime blocker.

### Phase 0B — Historical Data Foundation

Implemented:

- historical source/final importer;
- filename grouping for one-to-many reference sets;
- optional SKU/design extraction with `--id-regex`;
- explicit JSON pairing manifests;
- exact source deduplication plus alias-path tracking;
- conservative normalized/perceptual target deduplication;
- stable `artwork_identity`;
- SQLite Dataset Registry;
- versioned dataset manifests that reference original files rather than copying them;
- deterministic artwork-level train / validation / Golden Holdout splits;
- Golden Holdout exclusion from retrieval eligibility;
- dataset and historical-pair read APIs.

Folder import example:

```powershell
python -m pod_artwork_engine historical-import --source-dir "D:\\historical\\inputs" --target-dir "D:\\historical\\finals" --dataset-name historical
python -m pod_artwork_engine dataset-list
```

Explicit pairing can use a JSON manifest with `design_id`, `target`, `sources[]` and optional `metadata`.

### Phase 0C — Harness Foundation

Implemented:

- typed benchmark case and recipe schemas;
- Smoke / Regression / Golden case selection from Dataset Registry splits;
- deterministic raster comparison metrics for layout, color, texture/detail and technical fidelity;
- optional strict exact-text scoring when OCR/recognized text is supplied;
- operational metrics for latency, memory/VRAM, retries, provider calls and cost;
- per-case visual diff images;
- scorecards with metric coverage and cohort breakdowns;
- persisted harness runs under the tool data directory;
- Golden promotion guard that blocks serious cohort regressions even when the overall mean improves;
- CLI and read-only API endpoints for harness runs;
- 2 GB harness storage quota included in the global 40 GB tool budget.

Example recipe:

```json
{
  "recipe_id": "baseline-v1",
  "version": "1",
  "stages": [
    {"name": "reconstruction", "implementation": "provider-a", "version": "1"}
  ]
}
```

Candidate manifest keys may use `pair_id`, `case_id`, or `artwork_identity`:

```json
{
  "candidates": {
    "pair_...": {
      "result_path": "results/pair_....png",
      "recognized_text": ["EXACT TEXT"],
      "operational": {
        "latency_ms": 32000,
        "provider_calls": 1,
        "cost_usd": 0.04
      }
    }
  }
}
```

Run the harness:

```powershell
python -m pod_artwork_engine harness-cases historical-v1 --tier smoke
python -m pod_artwork_engine harness-run historical-v1 --tier smoke --recipe recipe.json --candidates candidates.json
python -m pod_artwork_engine harness-scorecards
```

Promotion comparison defaults to requiring Golden Holdout scorecards:

```powershell
python -m pod_artwork_engine harness-compare run_champion run_challenger
```

### Phase 1A — Reconstruction Pipeline Foundation

Implemented:

- enhanced pre-flight quality signals and artwork-region detection;
- local `DesignSpec` analyzer with strict typed contracts;
- capability router for deterministic, remote-semantic and hybrid paths;
- vendor-neutral remote provider gateway that can front GPT-6-family or other image/vision providers;
- typed provider boundary with bounded JSON repair and strict Pydantic validation;
- remote-first Quick 2D path with deterministic local fallback;
- local crop/alpha reconstruction baseline for clean/flat sources;
- 4500×5400 RGBA PNG export with 300-DPI metadata;
- semantic QC Gate #1 and technical print QC Gate #2;
- per-stage checkpoints, artifact manifest and resource telemetry;
- restart/resume through the full pipeline;
- local output API plus Desktop output preview/export button.

Remote-provider configuration is optional:

```text
POD_REMOTE_PROVIDER_URL=
POD_REMOTE_PROVIDER_TOKEN=
POD_REMOTE_PROVIDER_NAME=remote
POD_REMOTE_PROVIDER_TIMEOUT_SECONDS=120
```

The configured gateway receives typed `analyze`, `reconstruct` and, when policy requires it, `judge` requests. Local filesystem paths are never sent to the provider; references/candidates are normalized and transferred as encoded images. If the gateway is unavailable or returns invalid structured data, the engine falls back deterministically and records the reason.

Phase 1A does **not** claim exact typography reconstruction yet. OCR/font/layout rebuild, concrete production provider mappings and stronger semantic Judge integration remain the next Phase 1 work.

### Phase 1B — Text Fidelity + Semantic Judge

Implemented foundation:

- provider OCR output is merged into the versioned `DesignSpec.exact_text`;
- typed typography contracts store line order, normalized line boxes, font-family/weight hints, fill/stroke and confidence;
- deterministic typography renderer uses only an explicitly matched installed/local font and refuses silent fallback when the font or confidence is insufficient;
- local user font directory at `<data-root>/fonts`;
- Max Fidelity and difficult Print Ready jobs can invoke a separate semantic `judge` provider action using SOURCE + CANDIDATE + DesignSpec;
- Judge output is strictly typed and can score exact text, layout, object fidelity, color, texture and missing detail;
- object-fidelity thresholds are policy-controlled; the Judge does not directly decide production state;
- exact text remains a hard Print Ready / Max Fidelity gate;
- Harness candidate manifests can carry semantic Judge evidence so `semantic.object_fidelity` has measurable coverage instead of being silently omitted.

Still pending in Phase 1:

- production OCR/font-identification adapter and font-family normalization;
- deterministic vector/logo geometry reconstruction;
- mixed-art text replacement/masking rather than pure-typography-only deterministic redraw;
- concrete GPT-6/provider gateway recipe and benchmark-selected model mapping;
- real historical Smoke/Regression/Golden benchmark runs and threshold calibration.

See `docs/POD_ARTWORK_RECONSTRUCTION.md` for the canonical architecture.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pod_artwork_engine serve
```

Engine API defaults to `http://127.0.0.1:8765`.

Run tests:

```powershell
pytest
```

Build the standalone Windows release:

```powershell
powershell -NoProfile -File scripts\build_desktop.ps1
```

The build produces `build/release/PODArtworkTool.exe` as the stable bootstrap launcher plus the versioned desktop/engine payload and an update ZIP under `build/packages/`. The normal user entry point is `PODArtworkTool.exe`; Python or PowerShell is not required at runtime.

Desktop development uses the `desktop/` Tauri shell and the local engine process.
