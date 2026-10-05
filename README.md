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
POD_PROVIDER_RECIPE_PATH=config/provider-recipe.example.json
```

`ProviderRecipe` keeps model/provider selection outside core pipeline code. Each `analyze`, `reconstruct` and `judge` action can define a model alias, image transfer size, timeout and provider parameters. The example intentionally uses capability aliases such as `vision_reasoning`, `image_reconstruction` and `vision_judge`; real GPT or other provider model names should be selected from Harness evidence rather than hard-coded into the engine.

The configured gateway receives typed `analyze`, `reconstruct` and, when policy requires it, `judge` requests. Local filesystem paths are never sent to the provider; references/candidates are normalized and transferred as encoded images. If the gateway is unavailable or returns invalid structured data, the engine falls back deterministically and records the reason.

Phase 1A establishes the operational pipeline. Exact typography, semantic judging and deterministic vector/geometry precision are layered on top in the later Phase 1 increments below.

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

### Phase 1C — Deterministic Precision Foundation

Implemented:

- typed provider actions (`analyze`, `reconstruct`, `judge`) instead of free-form action strings;
- typography evidence provenance fields for OCR/font-identification adapters;
- explicit mixed-art text replacement policy: only regions marked `replace_solid` with a known replacement fill may be destructively replaced;
- illustration pixels outside approved replacement boxes remain untouched;
- deterministic typography failure escalates to the configured remote provider rather than silently substituting a different font;
- typed high-confidence logo geometry primitives for rectangle, ellipse, line and polygon;
- deterministic raster rendering for those primitives plus an SVG geometry master;
- geometry SVG is recorded in the final artifact manifest;
- geometry/text reconstruction refuses low-confidence primitives or missing evidence rather than guessing.

### Phase 1D — Local Text/Bezier + Real Engine Harness

Implemented:

- optional local Tesseract OCR adapter with confidence-filtered line ordering and normalized text boxes;
- OCR runs only when the source looks text/logo/mixed and the local executable is actually available, so machines without Tesseract do not pay OCR runtime cost;
- local OCR evidence is typed as `TypographySpec`, recorded in checkpoints and reused by QC/provider analysis;
- installed/local font catalog reads real family/style metadata through Pillow instead of relying only on filenames;
- canonical font-name aliases plus `<data-root>/font_aliases.json` override support;
- strict font-weight matching: a regular font is never silently used when the requested evidence says bold;
- cubic Bezier `path` geometry with deterministic raster approximation and exact SVG path retention;
- SVG colors are normalized/validated before writing so untrusted provider strings cannot be injected into vector markup;
- mixed-art text replacement supports explicit polygon masks, with a guard that refuses masks extending outside the approved text region;
- real `HarnessEngineRunner` executes the production engine against Dataset Registry cases instead of requiring pre-generated candidate manifests;
- benchmark recipes can select a provider-recipe file and local-OCR policy through typed metadata;
- scorecards report precision-path coverage such as local OCR, deterministic typography, mixed text refinement and geometry-vector use.

Run the real production engine through the Harness:

```powershell
python -m pod_artwork_engine harness-engine-run historical-v1 --tier smoke --recipe config/benchmark-recipe.local.json --quality-mode print_ready --limit 8
```

For a remote/GPT/JEV-gateway challenger, use `config/benchmark-recipe.remote.example.json`. Its `provider_recipe_path` is resolved relative to the benchmark recipe, while provider URL/token stay in local environment settings. This keeps concrete model names outside core engine code and lets Smoke/Regression/Golden evidence choose the mapping.

Local OCR configuration:

```text
POD_LOCAL_OCR_ENABLED=1
POD_TESSERACT_PATH=
POD_TESSERACT_LANGUAGE=eng
```

Tesseract is currently auto-detected or explicitly configured; it is **not yet bundled into the installer**. This is a concrete local OCR adapter/fallback foundation, not yet the final zero-dependency OCR packaging decision.

Still pending in Phase 1:

- choose/bundle the production OCR backend for fully standalone installs, or prove a remote/local hybrid wins on the Golden Holdout;
- visual font identification beyond provider hints plus exact installed-family normalization;
- multi-subpath/compound Bezier tracing and more complex logo topology;
- illustration-aware/inpainting masks for text that overlaps non-solid artwork;
- run the real historical Smoke → Regression → Golden benchmark sequence;
- calibrate QC/router thresholds from those scorecards;
- select concrete GPT/provider aliases and fallback mappings from measured quality/latency/cost rather than hard-coding them.

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
