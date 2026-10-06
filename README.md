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

### Phase 1E — Benchmark Suite + Safe QC Calibration

Implemented:

- `harness-suite` runs a champion and one or more challenger recipes through Smoke → Regression → Golden in order;
- challengers that fail Smoke/Regression stop early, reducing unnecessary provider/GPU work;
- Golden success is reported only as `eligible_for_human_review`; the Harness never rewrites production configuration or auto-promotes a challenger;
- scorecards now persist recipe SHA-256, Dataset Registry manifest SHA-256, engine version, execution kind, quality mode, provider-recipe identity and QC-policy identity;
- champion/challenger comparison rejects incomplete scorecards, dataset-manifest mismatches and quality-mode mismatches;
- production-engine Harness results carry the engine's semantic/technical QC evidence so threshold behavior can be compared against approved target quality;
- QC thresholds are now represented by a typed `QCPolicy`; the built-in defaults preserve the previous behavior;
- an explicit `POD_QC_POLICY_PATH` or benchmark recipe `qc_policy_path` can load a candidate policy for testing, but no calibration result is applied automatically;
- `harness-calibrate` labels benchmark outcomes from target-comparison quality, searches bounded thresholds under a false-accept constraint, and writes a review-only proposal plus `candidate-qc-policy.json`;
- safe calibration requires Golden Holdout runs by default and retains current values when judge/metric evidence is insufficient.

Run a full champion/challenger suite:

```powershell
python -m pod_artwork_engine harness-suite historical-v1 `
  --champion config/benchmark-recipe.local.json `
  --challenger config/benchmark-recipe.remote.example.json `
  --quality-mode print_ready
```

Generate a QC-policy proposal after Golden runs:

```powershell
python -m pod_artwork_engine harness-calibrate <golden-run-id> <another-golden-run-id>
```

Outputs are stored under `<data-root>/harness/suites/<suite-id>/` and `<data-root>/harness/calibration/<proposal-id>/`. A calibration proposal is evidence only. To test it, explicitly point `POD_QC_POLICY_PATH` or a benchmark recipe at the generated candidate file and run the Harness again.

### Phase 1F — Counterfactual Router Evidence + Safe Router Calibration

Implemented:

- routing thresholds now live in a typed `RouterPolicy` instead of being hard-coded in `router.py`;
- `POD_ROUTER_POLICY_PATH` can explicitly select a candidate policy; `config/router-policy.example.json` documents the built-in defaults and the built-in policy preserves the previous Phase 1 behavior;
- production runs record router-policy identity in the final artifact manifest and Harness provenance;
- Harness recipes may use `harness_route_override` to force `deterministic`, `hybrid`, or `remote_semantic` **only inside Harness execution**;
- per-case Harness results record selected route, requested override, design confidence, artwork type, capabilities, provider availability and whether a remote reconstruction actually executed;
- `harness-route-matrix` runs the same benchmark cases through deterministic and remote-capable routes and compares target quality case-by-case;
- a forced remote route that falls back locally is marked unusable for router learning, so fallback output is never mislabeled as remote evidence;
- `harness-router-calibrate` derives bounded confidence-threshold proposals only from valid counterfactual comparisons;
- router calibration uses a configurable false-local ceiling, where a false-local means choosing deterministic on a case where the measured remote route was materially better;
- Golden Holdout evidence is required by default; generated `candidate-router-policy.json` files are review-only and are never applied automatically;
- complex-route enable/disable booleans are intentionally not auto-learned yet; only thresholds with sufficient counterfactual evidence may change.

Create a deterministic-vs-hybrid route matrix:

```powershell
python -m pod_artwork_engine harness-route-matrix historical-v1 `
  --tier golden `
  --recipe config/benchmark-recipe.remote.example.json `
  --quality-mode print_ready `
  --route deterministic `
  --route hybrid
```

Generate a review-only RouterPolicy proposal:

```powershell
python -m pod_artwork_engine harness-router-calibrate <route-matrix-id>
```

Route matrix artifacts are stored under `<data-root>/harness/route-matrices/<matrix-id>/`; router proposals are stored under `<data-root>/harness/router-calibration/<proposal-id>/`. A working remote provider is required for genuine remote counterfactual evidence.

### Phase 1G — Guarded Visual Font Identification

Implemented:

- local visual font matching compares OCR/provider text crops against fonts already installed on Windows or placed in `<data-root>/fonts`; no model download is required;
- matching is color-independent: the matcher estimates foreground from alpha or border/background contrast before comparing normalized glyph silhouettes;
- candidate fonts are deduplicated, prefiltered by rendered aspect ratio, then scored by glyph-shape similarity plus aspect fidelity;
- a font is accepted only when both absolute score and best-vs-runner-up margin pass configured thresholds; ambiguous look-alike fonts remain unresolved;
- accepted evidence records family, style, inferred weight, score, margin, method, candidate count and the exact font-file SHA-256 in typed `FontMatchEvidence`;
- deterministic rendering verifies that SHA-256 before reusing a visually matched font, so a changed/replaced font file cannot silently alter a previously approved reconstruction;
- unresolved matches never overwrite `font_family`; therefore deterministic typography still refuses silent font substitution;
- verified local font evidence can replace a provider font guess for the same text line, while unmatched provider lines can be locally verified after remote analysis;
- font matching is optional and failure-isolated: matching errors fall back to OCR/provider analysis instead of failing the job;
- Harness precision evidence now reports `visual_font_match` coverage and matched-line counts;
- benchmark recipes may independently control font matching and its score/margin/candidate limits, allowing Golden Holdout data to calibrate the feature rather than hard-coding confidence assumptions.

Configuration:

```text
POD_VISUAL_FONT_MATCH_ENABLED=1
POD_VISUAL_FONT_MATCH_MIN_SCORE=0.72
POD_VISUAL_FONT_MATCH_MIN_MARGIN=0.035
POD_VISUAL_FONT_MATCH_MAX_CANDIDATES=96
```

`TypographyLine.bbox` used for visual verification is interpreted inside the detected artwork region. Curved, heavily warped, occluded or textured text may not produce a confident local match; those cases remain eligible for provider reconstruction/review rather than being forced into a wrong local font.

### Phase 1H — Compound Vector Topology

Implemented:

- a single typed `GeometryKind.PATH` may contain multiple subpaths, including multiple `MOVE ... CLOSE` contours and multiple open stroke-only contours;
- cubic Bezier commands remain supported inside every subpath, so compound logos can combine curved outer shapes, inner counters/holes and separate marks without flattening them into unrelated raster layers;
- `GeometryFillRule` is explicit in the contract and SVG output records `fill-rule` instead of relying on renderer defaults;
- deterministic raster rendering supports compound filled paths only with explicit `evenodd`, which gives predictable holes/counters independent of contour winding direction;
- compound `nonzero` fills still export correctly to SVG but intentionally fail closed in the Pillow raster path rather than risk filling a hole incorrectly;
- filled compound paths must close every subpath; malformed, empty, degenerate or post-close commands are rejected before rendering;
- open multi-subpath paths are supported when they are stroke-only;
- every deterministic geometry result records typed topology provenance: primitive count, path count, total subpaths, compound paths, even-odd compound fills, evidence provider/version and the SVG SHA-256;
- the topology evidence is included in the job checkpoint, ArtifactManifest precision evidence and Harness precision metrics;
- Harness now reports `compound_geometry` coverage in addition to ordinary `geometry_vector` coverage.

This keeps the precision route conservative: SVG can preserve richer provider/vector topology, while local raster output is produced only when the fill semantics are deterministic and tested.

### Phase 1I — Guarded Illustration-Aware Text Repair

Implemented:

- mixed-art typography can explicitly request `repair_local` when text overlaps a gradient, illustration or other non-solid artwork where flat replacement would leave a visible patch;
- local repair uses deterministic horizontal and vertical boundary interpolation inside the approved polygon mask, preserving simple gradients and nearby color transitions without a generative model;
- `repair_local` requires an explicit polygon mask plus `replacement_confidence`; the default minimum confidence is `0.82`;
- repair masks retain the existing guarded text-region boundary checks, so a provider cannot use text repair to rewrite unrelated parts of the illustration;
- the repair path fails closed if it cannot reconstruct every masked pixel from valid surrounding boundary evidence;
- the algorithm uses Pillow only, adds no new local model/OpenCV dependency, and therefore has negligible impact on the 40 GB tool-storage budget;
- successful jobs record `local_text_repair` precision provenance with method, threshold, repaired lines, confidence and mask-point counts in checkpoints and the final ArtifactManifest;
- Harness `PrecisionEvidence` reports whether guarded repair ran and how many text regions were repaired, while benchmark recipes can independently enable it and vary the confidence threshold.

Configuration:

```text
POD_LOCAL_TEXT_REPAIR_ENABLED=1
POD_LOCAL_TEXT_REPAIR_MIN_CONFIDENCE=0.82
```

This is intentionally a conservative deterministic repair path, not unrestricted semantic inpainting. Highly textured, occluded or structurally complex regions should still escalate to provider reconstruction/region rescue or manual review.

Still pending in Phase 1:

- import/run the user's real historical source/final pairs through the benchmark suite and route matrix, then establish the first measured champion/router policy;
- choose/bundle the production OCR backend for fully standalone installs, or prove a remote/local hybrid wins on Golden Holdout;
- calibrate visual-font score/margin thresholds on real historical typography cases and build a curated user-font library where licensing permits;
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
