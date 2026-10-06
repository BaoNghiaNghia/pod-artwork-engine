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

Tesseract can be explicitly configured, discovered from the host, or supplied as the bundled `runtime/tesseract/` sidecar described in Phase 1J. The repository does not commit third-party OCR binaries; approved release binaries/language packs are supplied through `vendor/tesseract/` at build time.

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

### Phase 1J — Standalone OCR Runtime Packaging

Implemented:

- Tesseract discovery is deterministic: explicit `POD_TESSERACT_PATH` → bundled runtime beside the engine → system `PATH` → standard Windows install folders;
- standalone releases use the layout `runtime/tesseract/tesseract.exe` plus `runtime/tesseract/tessdata/*.traineddata`;
- when a sibling `tessdata` directory exists, the OCR subprocess receives a scoped `TESSDATA_PREFIX` without mutating the parent process environment;
- OCR checkpoints now record backend name, backend version, discovery source and executable SHA-256, and ArtifactManifest retains that provenance;
- API status and diagnostic bundles expose only redacted backend metadata such as `bundled`, `configured` or `path`; they do not expose the executable path;
- Harness precision evidence retains OCR backend source/version so bundled and system-backed runs can be compared separately;
- release assembly automatically copies `vendor/tesseract/` into `runtime/tesseract/` when an approved runtime is present;
- updater validation rejects partial bundled runtimes such as an executable without any `tessdata/*.traineddata`;
- initial release seeding, staged updates and rollback releases preserve the optional `runtime/` tree;
- `vendor/tesseract/` is Git-ignored so OCR binaries/language data are never accidentally committed to source control.

To build a release with standalone OCR, place the approved redistributable runtime before running the normal desktop build:

```text
vendor/
  tesseract/
    tesseract.exe
    tessdata/
      eng.traineddata
      ...other approved languages...
```

The source tree intentionally does not embed third-party OCR binaries. Redistribution/licensing review and the exact language-pack set remain release inputs, while the runtime/search/update path is now production-ready.

### Phase 2A — Guarded Multi-Reference Evidence Fusion

Implemented:

- jobs with multiple source references now build typed `MultiReferenceFusionEvidence` before semantic analysis;
- every reference receives a deterministic quality score from artwork-region confidence, source quality and useful resolution;
- the highest-quality reference becomes one stable primary source reused by local analysis, OCR, deterministic reconstruction and source provenance;
- cropped artwork fingerprints combine a lightweight difference hash, mean RGB evidence and aspect ratio to classify each additional reference as `consistent`, `ambiguous` or `conflicting`;
- the engine does **not** average/blend pixels between references, so different views cannot silently smear or invent artwork details;
- high-quality visual conflicts add `need_reference_disambiguation` and `need_semantic_reconstruction`; when a provider is available the router explicitly selects a Hybrid semantic-disambiguation path;
- provider analysis/reconstruction still receives the complete reference set, while deterministic local stages stay anchored to the selected primary reference;
- if semantic disambiguation is required but unavailable, existing Print Ready / Max Fidelity semantic QC remains fail-closed instead of pretending the conflict was resolved;
- fusion evidence is checkpointed, copied into ArtifactManifest precision evidence and reported through Harness as reference count, consistent/conflicting counts and consensus confidence;
- single-reference jobs preserve their existing behavior;
- the fusion stage is Pillow/CPU-only and stores only compact metadata, so it adds negligible disk footprint under the 40 GB cap.

The initial visual-similarity thresholds are conservative implementation defaults, not learned production optima. They must be evaluated on the real historical/Golden Holdout set before being promoted as domain-calibrated policy.

### Phase 2B — Coarse Region Confidence Evidence Map

Implemented:

- every job now records a deterministic `RegionConfidenceMapEvidence` checkpoint over a 4×4 grid in normalized primary-artwork coordinates;
- only references already classified as globally `consistent` can support a region, and they must also have a sufficiently compatible artwork-crop aspect ratio;
- ambiguous/conflicting references, aspect-incompatible views and exact duplicate source hashes are excluded from increasing regional confidence;
- compatible supporting crops are normalized only for coarse comparison; the engine still performs no image blending, multi-view warping or pixel replacement;
- each region records confidence, local visual agreement, independent support count, comparison count and reason codes such as `local_disagreement`, `primary_only` or `low_confidence`;
- local agreement combines lightweight block structure and mean-color similarity, allowing a globally consistent reference to reveal one locally inconsistent quadrant without invalidating every region;
- aggregate evidence records mean/minimum confidence, support coverage and low-confidence cell count;
- the region map is persisted into ArtifactManifest precision evidence and surfaced in Harness for later rescue/QC calibration;
- if region evidence cannot be computed (for example, no reliable artwork bbox), the feature degrades to an unavailable/zero-confidence metadata record and does not fail the job;
- this phase does not alter reconstruction pixels or trigger automatic repair yet; it establishes the compact evidence layer future region rescue can consume;
- the implementation remains CPU/Pillow-only and adds no model/cache footprint beyond small JSON checkpoint data.

The normalized-grid comparison is intentionally conservative and is not equivalent to geometric registration. Side views, perspective changes and occluded references can be excluded or show low confidence until a future alignment/dewarp stage is benchmarked.

### Phase 2C — Fail-Closed Region Rescue Planning

Implemented:

- every job now converts the region confidence map into a typed `RegionRescuePlanEvidence` checkpoint;
- only cells below the conservative confidence policy (or explicitly marked local disagreement/no consensus) become rescue candidates;
- adjacent cells are merged only when they form exact rectangular runs; irregular shapes stay split so a rescue bbox never expands across a known-good cell;
- plans have exactly three dispositions: `none`, `semantic_provider_candidate` or `manual_review`;
- global multi-reference conflicts and unavailable region evidence always produce `manual_review` with `fail_closed=true`;
- low-confidence regions may become semantic-provider candidates only when a provider is available; this phase records that eligibility but does not execute reconstruction;
- when no provider is available, target regions are retained for review while the plan fails closed;
- each target stores normalized bbox, exact contributing grid cells, mean/max confidence, reason codes and required capabilities;
- ArtifactManifest precision evidence and Harness now record planner presence, disposition, target count/cell count and fail-closed state;
- the production pipeline does not transition into `REGION_RESCUE`, call a provider, retry, or modify pixels in Phase 2C;
- the planner is deterministic metadata only and adds no meaningful storage/model footprint.

This establishes a measurable boundary between “a weak region exists” and “the system is authorized to alter that region.” Actual rescue execution remains disabled until Golden Holdout evidence establishes safe trigger thresholds and provider/local rescue behavior.

### Phase 2D — Conservative Vector/Raster Representation Planning

Implemented:

- every completed analysis now produces a typed `RepresentationPlanEvidence` checkpoint before routing/reconstruction;
- planning is component-level: `typography`, `geometry`, `illustration_texture` and fail-closed `unknown_content`;
- overall representation is derived as `vector`, `raster` or `hybrid` from component evidence;
- typography becomes vector-ready only when text/layout confidence is sufficient, font families are resolved, aggregate font-match confidence is high enough and every line has an accepted font-match record;
- unverified/missing typography falls back to raster and records missing capabilities such as font identification/verification, exact-text verification or layout verification;
- logo/geometry becomes vector-ready only with explicit geometry primitives plus sufficient geometry and primitive confidence; otherwise it remains raster with missing vector-geometry evidence recorded;
- illustration, texture, occlusion, perspective and semantic reconstruction content stay raster by design in this phase;
- mixed artwork can therefore plan a hybrid representation, for example verified text as vector plus painterly illustration as raster;
- unknown/unclassified content is preserved as raster and marks the plan fail-closed rather than guessing a vector structure;
- representation plans are copied into ArtifactManifest precision evidence and Harness reports overall mode, vector/raster component counts and fail-closed status;
- API/diagnostics expose only the planner method/version, not private content;
- Phase 2D does **not** change the existing renderer, route policy, provider calls or final pixels; it is evidence/planning only;
- the planner is pure metadata logic and adds no model/cache footprint to the 40 GB budget.

This makes the future vector/raster split measurable before any renderer behavior is changed: Golden Holdout can compare planned representation against successful deterministic operations and QC outcomes.

### Phase 2E — Material-Separation Evidence Planning

Implemented:

- the selected primary reference now receives a typed `MaterialSeparationEvidence` checkpoint after DesignSpec analysis;
- source alpha is inspected directly rather than trusting the presence of an alpha channel: meaningful transparent/visible coverage is required before classifying `existing_alpha`;
- opaque RGBA inputs are explicitly distinguished from real transparent artwork;
- simple border-background separation is only marked as a candidate when the source border is highly uniform, artwork localization is sufficiently confident, foreground/background contrast is measurable and the artwork does not touch the source edge;
- border uniformity is derived from lightweight source-edge RGB statistics and foreground contrast is measured against the detected artwork region;
- nonuniform backgrounds, edge-touching artwork, illustration/mixed material, fine-detail texture, strong perspective/occlusion, heavy compression or an explicit semantic-reconstruction requirement classify as `semantic_required`;
- weak localization, very weak source quality, missing bbox or insufficient foreground/background evidence classify as `manual_review`;
- every record carries confidence, fail-closed state, normalized artwork bbox, alpha coverage, border uniformity, edge-contact ratio, foreground contrast, reason codes and missing capabilities;
- ArtifactManifest precision evidence and Harness now expose the material-separation disposition and metrics so Golden Holdout can measure when simple extraction is actually safe;
- API/diagnostics expose only the method version `material_separation_evidence_v1`;
- Phase 2E does **not** alter `_extract_alpha_from_background`, provider routing, candidate pixels or final output; `simple_border_background` is evidence/readiness only;
- the implementation uses Pillow statistics only and adds no model/cache footprint to the 40 GB budget.

The initial thresholds are conservative engineering defaults and must be calibrated on historical/Golden Holdout cases before material-separation evidence is allowed to control reconstruction.

### Phase 2F — Difficult-Texture Evidence Planning

Implemented:

- the selected primary artwork now receives typed `TextureHandlingEvidence` after material-separation evidence is available;
- texture disposition is one of `preserve_raster`, `local_detail_enhancement_candidate`, `semantic_required` or `manual_review`;
- the planner measures lightweight edge density, local contrast, local pixel variation and native artwork long-edge resolution using Pillow only;
- texture decisions also consume source quality, compression risk, normalized region confidence, material-separation disposition and DesignSpec texture/perspective/occlusion evidence;
- painterly/smooth and adequately sourced fine-detail artwork default to raster preservation rather than speculative sharpening;
- outlined artwork is only marked as a local-detail-enhancement candidate when source quality/resolution, compression, region confidence, edge density, contrast and variation all pass conservative readiness thresholds;
- fine-detail artwork with weak source quality, strong compression, low native resolution or weak regional evidence fails closed to semantic reconstruction;
- perspective, occlusion or an explicit semantic-reconstruction requirement also force semantic handling;
- unresolved material separation or unavailable/very weak regional evidence force manual review before any texture operation;
- Harness and ArtifactManifest now record disposition, confidence, fail-closed state, edge density, local contrast/variation and native artwork resolution;
- API/diagnostics expose only the method version `texture_handling_evidence_v1`;
- Phase 2F does **not** sharpen, upscale, run SR, alter provider routing or mutate candidate/final pixels; `local_detail_enhancement_candidate` is readiness metadata only;
- the planner adds no model/cache footprint and therefore remains compatible with the 40 GB storage limit.

These thresholds are deliberately conservative and must be calibrated against Golden Holdout texture/detail cohorts before any sharpening/SR/detail-recovery execution is enabled.

### Phase 2G — Super-Resolution Readiness Foundation

Implemented:

- every analyzed primary artwork now receives typed `SuperResolutionReadinessEvidence` after material and texture evidence are available;
- disposition is one of `native_sufficient`, `local_sr_candidate`, `remote_sr_candidate` or `manual_review`;
- readiness uses the detected native artwork dimensions and the same `ExportProfile` geometry used by final export to estimate the actual scale factor needed to fit the POD print target;
- the evidence records native/target artwork width and height, native/target long edge, QC native-resolution floor, scale factor, source quality/compression, region confidence and upstream material/texture dispositions;
- native artwork is accepted without SR only when print-target scaling is bounded (initially `<=1.25×`) and the quality-mode native-resolution floor is already satisfied;
- local SR is only a benchmark candidate for bounded `<=2×` scaling when the texture planner already marked local detail enhancement safe and source quality/compression/region evidence are strong;
- remote SR is only a benchmark candidate up to `4×` when source quality, compression and region evidence pass conservative thresholds;
- remote-SR candidates record provider availability; unavailable provider capability fails closed instead of pretending execution is possible;
- scale factors above `4×`, very weak/compressed sources, unresolved semantic/material/texture dependencies or insufficient region evidence require manual review/higher-resolution references;
- `execution_enabled=false` is explicit in every Phase 2G evidence record: no SR model or provider is invoked;
- ArtifactManifest and Harness now expose SR disposition, confidence, scale factor, native/target long edge and provider availability;
- API/diagnostics expose only the method version `super_resolution_readiness_v1`;
- Phase 2G adds metadata only, with no model/cache footprint and no change to candidate/final pixels.

The readiness thresholds define benchmark cohorts, not production SR policy. Local and remote SR must beat native/Lanczos output on Golden Holdout quality, small-detail survival, latency and cost before any execution path is enabled.

### Phase 2H — Native / Local-SR / Remote-SR Benchmark Matrix

Implemented:

- a typed `SRBenchmarkSpec` / `SRBenchmarkReport` matrix compares `native`, `local_sr` and `remote_sr` lanes on the exact same Dataset Registry cases;
- the native lane can run the real production engine automatically, or consume an explicit CandidateManifest when an already-materialized baseline is preferred;
- local-SR and remote-SR lanes are pluggable CandidateManifest adapters only: if no concrete backend/output manifest exists, the lane is reported as `unavailable` instead of generating fake SR output or fake scores;
- every available lane is evaluated by the existing Harness against the same target images and must share the same dataset-manifest fingerprint;
- per-lane evidence includes quality/technical means, small-detail survival, p50/p95 latency, peak RAM/VRAM, provider calls, cost and manual-review count;
- per-case evidence preserves quality, semantic/technical scores, small-detail survival, effective resolution, latency, memory/VRAM, provider/cost evidence, manual-review and SR fail-closed state;
- an SR challenger is promotable only when both quality and small-detail survival improve past configured floors, semantic score stays within the allowed regression bound, and manual-review/fail-closed state is absent;
- optional latency-ratio and cost-per-case ceilings can independently reject an otherwise higher-quality SR challenger;
- a conservative `hallucination_risk` signal is raised when exact text, object-fidelity evidence or aggregate semantic score regresses beyond the configured bound; this is a benchmark safety signal, **not** a claim that the Harness has a complete hallucination detector;
- reports recommend `keep_native`, `keep_lanczos`, or a local/remote/mixed SR policy **for human review**; when a valid Lanczos cohort lane is supplied, SR promotion is measured against Lanczos instead of the smaller native source; `auto_applied=false` and `production_execution_enabled=false` are fixed;
- matrix artifacts are persisted under the Harness `sr-matrices/` directory for later Golden Holdout review/calibration;
- API/diagnostics expose `sr_benchmark_matrix_v1` as the capability version;
- the matrix adds no SR model dependency and therefore does not consume the 40 GB tool-storage budget beyond compact reports/diffs and whatever candidate images the operator explicitly benchmarks.

Example:

```powershell
python -m pod_artwork_engine harness-sr-matrix historical-v1 --tier golden --recipe config/benchmark-recipe.local.json --native-candidates native-candidates.json --lanczos-candidates lanczos-candidates.json --local-sr-candidates local-sr-candidates.json --remote-sr-candidates remote-sr-candidates.json --min-quality-gain 0.01 --min-detail-gain 0.03
```

Omit `--native-candidates` to generate the native baseline with the current production engine. Omit either SR candidate manifest when that backend is not available; the missing lane remains explicitly unavailable and cannot win promotion.

### Phase 2I — Concrete SR Adapter Foundation

Implemented:

- typed `SRAdapterSpec` and `SRAdapterRunReport` contracts for benchmark-only local and remote SR materialization;
- `local_command` adapters execute an explicitly configured command as an argument list with `shell=false`; supported placeholders are `{input}`, `{output}`, `{scale}`, `{pair_id}` and `{case_id}`;
- local adapters require both `{input}` and `{output}`, verify the executable is available before the run, measure elapsed time and peak process-tree RAM, and kill the whole process tree on timeout;
- `remote_provider` adapters use the existing provider gateway with the new `super_resolution` action; the action must be explicitly present and enabled in the provider recipe, so existing providers cannot accidentally start running SR;
- both adapters consume an existing CandidateManifest as their input cohort and emit a new CandidateManifest directly consumable by Phase 2H;
- missing input candidates, unavailable backends, missing outputs, invalid images, insufficient upscale, aspect-ratio distortion or oversized outputs become explicit fail-closed/manual-review candidate entries instead of synthetic success;
- output validation records requested/measured scale and input/output dimensions, and rejects requested outputs above the configured megapixel safety ceiling before expensive execution;
- a conservative storage-headroom check blocks an SR candidate when its estimated uncompressed output would exceed the global 40 GB hard cap;
- candidate metadata records adapter id/version/kind/model alias, backend availability, backend reason codes, measured scale, fresh remote OCR/Judge evidence when supplied, latency, RAM/VRAM, provider calls and cost;
- `hallucination_risk` is propagated only when the concrete backend explicitly reports it; the adapter does not invent that signal;
- adapter runs persist under `<harness>/sr-adapters/<run_id>/` with spec, report, candidate images and manifest;
- CLI `harness-sr-materialize` materializes a real SR candidate manifest without adding any SR call to production `Engine.run_job`;
- `config/sr-adapter.local.example.json` and `config/sr-adapter.remote.example.json` provide safe templates;
- the provider recipe example exposes `super_resolution` disabled by default as an explicit opt-in benchmark action;
- API/diagnostics expose `sr_adapter_materializer_v1`;
- Phase 2I bundles no SR model weights or runtimes, so model storage remains zero until an operator explicitly installs/configures a backend.

Example local materialization:

```powershell
python -m pod_artwork_engine harness-sr-materialize historical-v1 --tier golden --input-candidates native-resolution-candidates.json --adapter config/sr-adapter.local.example.json
```

Then pass the generated manifest to Phase 2H as `--local-sr-candidates` or `--remote-sr-candidates`. The input manifest should represent the image stage intended for SR benchmarking; blindly applying 2× SR to an already-final 4500×5400 print master is intentionally constrained by the megapixel guard and is not the recommended comparison design.

### Phase 2J — Fair Native/Lanczos Pre-SR Cohort Foundation

Implemented:

- typed `SRCohortSpec` / `SRCohortReport` contracts create one benchmark-only pre-SR cohort for a Dataset Registry tier;
- the cohort consumes an explicit pre-SR CandidateManifest and never uses historical `target_path` as an input; direct target paths and normalized-artwork-equivalent target copies are rejected as ground-truth leakage;
- each valid input is EXIF-normalized once into `<harness>/sr-cohorts/<cohort_id>/source/`; the `source` and `native` manifests point to the same normalized pixels, so native performs no enlargement;
- a deterministic Pillow Lanczos baseline is generated from those exact normalized pixels at the requested scale factor;
- source/native/Lanczos entries preserve pair/case/artwork identity and record one `cohort_id + source_input_sha256` signature plus input/output dimensions and transform provenance;
- Local/Remote SR adapters now propagate that same cohort signature from the source manifest into their output candidates;
- Phase 2H accepts `--lanczos-candidates`; when Lanczos evidence is valid and shares the same cohort signature, SR gain is measured against Lanczos rather than native;
- a challenger with a different cohort id or source hash is rejected with `pre-SR cohort source mismatch`, preventing cross-input benchmark wins;
- missing inputs, target leakage, invalid images, megapixel overflow or 40 GB hard-cap pressure fail closed instead of materializing fake baselines;
- generated source/native/Lanczos images and manifests remain under Harness storage, and the phase adds no model weights;
- CLI `harness-sr-cohort` creates the fair cohort; API/diagnostics expose `sr_fair_cohort_v1`;
- `Engine.run_job`, RouterPolicy and production pixels remain unchanged.

Example workflow:

```powershell
python -m pod_artwork_engine harness-sr-cohort historical-v1 --tier golden --input-candidates pre-sr-candidates.json --scale-factor 2
```

Use the returned `source_manifest_path` as the input for both Phase 2I Local/Remote SR materializers, then pass the returned `native_manifest_path` and `lanczos_manifest_path` plus the SR manifests into `harness-sr-matrix`.

### Phase 2K — Golden SR Experiment Orchestration & Policy Proposal

Implemented:

- typed `SRExperimentSpec`, `SRExperimentReport` and `SRPolicyProposal` contracts orchestrate the full benchmark-only SR decision flow;
- one command chains Phase 2J fair cohort materialization, optional Phase 2I Local/Remote SR adapters, Phase 2H native/Lanczos/SR matrix scoring and the final evidence-only policy proposal;
- Golden Holdout is required by default; non-Golden runs require explicit `--allow-pre-golden` and are intended only for diagnostics;
- Local/Remote adapter specs must match their expected backend kind and the exact cohort scale factor, preventing unfair cross-scale comparisons;
- omitted or unavailable adapters remain unavailable lanes; the orchestrator does not fabricate candidate quality;
- policy evidence requires the same dataset fingerprint, a complete source-identical cohort, a configurable minimum comparable-case count, an incomplete-case ceiling and at least one measured SR backend;
- a configured backend whose benchmark run is partial or has missing successful cases blocks policy sufficiency instead of being treated as valid evidence;
- Local/Remote/Mixed SR proposals additionally require a configurable minimum decisive-win count;
- proposal outcomes are `keep_native`, `keep_lanczos`, `local_sr`, `remote_sr`, `mixed` or `manual_review`;
- when evidence gates fail, the proposal is forced to `manual_review` with `sufficient_evidence=false`;
- all proposals keep `requires_human_approval=true`, `automatically_applied=false` and `production_execution_enabled=false`;
- artifacts are persisted under `<harness>/sr-experiments/<experiment_id>/` with references to cohort, adapter and matrix reports;
- CLI `harness-sr-experiment` runs the complete flow; API/diagnostics expose `sr_golden_experiment_v1`;
- Phase 2K still does not modify `Engine.run_job`, RouterPolicy or production pixels.

Example Golden Holdout run:

```powershell
python -m pod_artwork_engine harness-sr-experiment historical-v1 --pre-sr-candidates pre-sr-candidates.json --recipe config/benchmark-recipe.local.json --local-adapter config/sr-adapter.local.example.json --remote-adapter config/sr-adapter.remote.example.json --min-comparable-cases 3 --min-decisive-wins 2
```

If one SR backend is not configured, omit that adapter flag. The resulting proposal remains evidence-only and cannot activate SR in production.

### Phase 2L — Geometric Multi-Reference Alignment / Dewarp Foundation

Implemented:

- typed `ReferenceAlignmentDisposition`, `ReferenceAlignmentEvidence` and `MultiReferenceAlignmentEvidence` contracts;
- primary reference identity is represented explicitly in normalized coordinates when its artwork bbox is trustworthy;
- globally consistent secondary references can become deterministic bbox-based `affine_candidate` entries with a 3×3 scale/translate matrix mapping the secondary normalized artwork bbox onto the primary bbox;
- every candidate records normalized source/target quadrilaterals, geometry confidence, aspect compatibility, optional perspective/occlusion evidence, reason codes and missing capabilities;
- affine matrices are deterministic and serialized with stable numeric precision, but `execution_enabled=false`: Phase 2L does not warp any production pixels;
- bbox correspondence is not misreported as feature reprojection: `reprojection_error` remains unset until real point/feature correspondence exists;
- moderate perspective/aspect shift becomes a fail-closed `homography_candidate` requiring feature correspondence + benchmark evidence rather than inventing a projective matrix;
- severe perspective, occlusion or strong aspect incompatibility becomes `semantic_required`;
- ambiguous/conflicting references, missing artwork localization or low-confidence localization become `manual_review` and are excluded from alignment support;
- alignment evidence is persisted immediately after reference fusion in preflight, included in artifact manifests and exposed to Harness precision evidence/coverage;
- API/diagnostics expose `geometric_reference_alignment_v1`;
- the current 4×4 region-confidence implementation is intentionally unchanged; Phase 2L prepares the shared-coordinate evidence contract before registration-aware regional sampling is enabled;
- no OpenCV/model dependency was added, and `Engine.run_job`, RouterPolicy, precision ops and production pixels remain unchanged.

This phase is an alignment **planning/readiness** foundation. Full feature matching, homography estimation, dewarp execution and region-level compositing still require Golden Holdout calibration.

### Phase 2M — Feature Correspondence & Measured Homography Benchmark Foundation

Implemented:

- typed per-match and per-reference correspondence evidence with `identity`, `measured_affine`, `measured_homography`, `insufficient_features`, `semantic_required` and `manual_review` dispositions;
- Pillow/Python-only grayscale patch matching on texture-rich deterministic grid anchors; no NumPy, OpenCV or model weights are added;
- patch candidates are gated by local texture, normalized patch error and uniqueness margin before entering geometric fitting;
- correspondences are stored as normalized source/target points with patch error and uniqueness evidence;
- bounded deterministic RANSAC fits affine and projective models, then refits on inliers using a local Gaussian-elimination least-squares solver;
- evidence records match/inlier counts, inlier ratio, artwork-relative spatial coverage, mean/median reprojection error and the measured 3×3 matrix;
- measured homography is allowed only when Phase 2L already identified a homography candidate and the projective fit materially improves on affine evidence;
- too few features, poor spatial coverage, low inlier ratio or high reprojection error fail closed instead of authorizing a warp;
- conflicting/ambiguous/manual references are excluded before patch matching; semantic-required alignment stays semantic-required;
- checkpoint `feature_correspondence`, artifact-manifest evidence and Harness precision/coverage fields are persisted;
- API/diagnostics expose `feature_correspondence_benchmark_v1`;
- `execution_enabled=false` for every result: measured transforms are benchmark evidence only and are not applied to region sampling, reconstruction or final pixels.

Recipes are now `local-precision-v18` / `remote-balanced-v18`, phase `phase2m`.

### Phase 2N — Golden Registration Calibration & Dewarp Promotion Policy

Implemented:

- Golden-only registration calibrator over persisted Harness case results;
- Harness engine case metadata now carries the raw `feature_correspondence` checkpoint so calibration reuses measured evidence without rerunning reconstruction;
- Phase 2M evidence now records both affine and homography candidate inlier/error metrics plus the homography-vs-affine median-error ratio;
- conservative p10/p90 calibration derives minimum match/inlier count, minimum inlier ratio, minimum artwork-relative spatial coverage, maximum mean/median reprojection error, and homography improvement ratio;
- calibrator rejects non-Golden runs, dataset/fingerprint mismatches and incomplete runs;
- minimum Golden case count is enforced per affine/homography lane;
- poor inlier/reprojection distributions or excessive manual-review/insufficient/semantic blocker rate force `manual_review`;
- successful lanes are only promoted to `*_for_dewarp_benchmark`, never to production execution;
- proposals persist under Harness `registration-calibration/<proposal_id>/proposal.json` with run/recipe/dataset provenance;
- CLI: `harness-registration-calibrate <run_id> [<run_id> ...]`;
- API/diagnostics expose `golden_registration_calibration_v1`;
- every proposal has `requires_human_approval=true`, `automatically_applied=false`, and `production_execution_enabled=false`.

Recipes are now `local-precision-v19` / `remote-balanced-v19`, phase `phase2n`.

### Phase 2O — Benchmark-only Dewarp Execution & Registration-aware Region Sampling

Implemented:

- explicit Golden-only dewarp materializer consuming a Phase 2N `RegistrationPolicyProposal` plus one of its source Golden runs;
- benchmark policy validation rejects dataset/run provenance mismatches, non-Golden policy sources, insufficient calibration, auto-applied policy or any policy with production execution enabled;
- affine/homography references must independently satisfy calibrated match/inlier/coverage/reprojection thresholds before dewarp;
- full-image normalized correspondence matrices are composed into artwork-crop coordinates before pixel execution, avoiding direct misuse of whole-image transforms on cropped artwork;
- Pillow perspective transform performs inverse-mapped benchmark warp with no NumPy/OpenCV/model dependency;
- paired manifests preserve the same secondary reference as an unregistered native control and a registered/dewarped challenger;
- if dewarp fails policy thresholds, the native control is still materialized whenever measured source evidence is usable;
- registration-aware 4×4 evidence compares primary-vs-native and primary-vs-dewarped local similarity per cell, recording mean delta and improved-cell count without replacing production `region_confidence_map`;
- Golden dewarp matrix scores native and registered manifests with the normal Harness metrics and records quality, technical, small-detail, failure-rate and manual-review deltas;
- recommendation is limited to `keep_native`, `dewarp_for_human_review`, `manual_review` or `insufficient_evidence`;
- CLI: `harness-dewarp-materialize` and `harness-dewarp-matrix`;
- API/diagnostics expose `benchmark_dewarp_registration_v1`;
- `Engine.run_job` remains unchanged: no dewarp checkpoint, no dewarp precision op and no production final-pixel mutation.

Recipes are now `local-precision-v20` / `remote-balanced-v20`, phase `phase2o`.

### Phase 2P — Golden Dewarp Experiment & Production Registration Policy Proposal

Implemented:

- one Golden-only orchestrator chaining Phase 2N calibration → Phase 2O materialization → native-vs-dewarp Harness matrix → production-registration policy proposal;
- calibration provenance must match the requested dataset/run set and remain Golden-only;
- when calibration is insufficient/manual-review, the experiment fails closed and persists an `insufficient_evidence` proposal without attempting dewarp execution;
- the materialization source run is explicit or deterministically selected from complete Golden calibration runs;
- dataset fingerprints are checked across calibration, dewarp cohort and matrix evidence;
- production-readiness gates require minimum comparable cases, minimum quality gain, no technical or small-detail regression, no failure/manual-review regression, bounded materialization failures and sufficient registration-aware region improvement;
- proposal recommendations are restricted to `candidate_for_human_approval`, `keep_disabled`, `manual_review` or `insufficient_evidence`;
- successful proposals preserve calibrated affine/homography thresholds so human review sees the exact evidence gates being proposed;
- experiment artifacts persist under `dewarp-experiments/<experiment_id>/` with spec, report and `production-policy-proposal.json`;
- CLI: `harness-dewarp-experiment`;
- API/diagnostics expose `golden_dewarp_experiment_v1` and `production_registration_policy_proposal_v1`;
- every proposal keeps `requires_human_approval=true`, `automatically_applied=false`, and `production_execution_enabled=false`; no production pixel path is changed.

Recipes are now `local-precision-v21` / `remote-balanced-v21`, phase `phase2p`.

### Phase 2Q — Material Separation Benchmark Execution & Golden Policy Calibration

Implemented:

- benchmark-only paired `native_unseparated` and `separated` manifests driven by the production `material_separation_evidence_v1` checkpoint;
- `existing_alpha` is preserved as a no-op benchmark challenger rather than re-segmented;
- `simple_border_background` uses deterministic Pillow-only border-color distance with hard/soft alpha thresholds;
- `semantic_required`, `manual_review`, weak-confidence and edge-touching cases fail closed while preserving a native control whenever the source is readable;
- Harness engine source runs now persist raw material-separation evidence in result metadata for reproducible Golden materialization;
- Golden matrix records quality, semantic, technical, alpha, halo, small-detail, failure-rate and manual-review deltas;
- material-separation promotion requires sufficient comparable Golden cases and measured quality or alpha benefit with no semantic/technical/detail/halo/failure/manual-review regression;
- Golden experiment persists `material-separation-experiments/<experiment_id>/spec.json`, `report.json` and `policy-proposal.json`;
- policy recommendations are limited to `candidate_for_human_approval`, `keep_disabled`, `manual_review` or `insufficient_evidence`;
- CLI: `harness-material-separation-materialize`, `harness-material-separation-matrix`, `harness-material-separation-experiment`;
- API/diagnostics expose `material_separation_benchmark_v1`, `golden_material_separation_experiment_v1` and `material_separation_policy_proposal_v1`;
- every benchmark/policy artifact remains `production_execution_enabled=false`; `Engine.run_job` still records evidence only and does not add a material-separation precision operation.

Recipes are now `local-precision-v22` / `remote-balanced-v22`, phase `phase2q`.

### Phase 2R — Immutable Human Policy Review Packet Boundary

Implemented:

- one fail-closed review-packet builder accepts Phase 2P production-registration proposals or Phase 2Q material-separation proposals;
- proposal files are parsed through their strict typed contracts and fingerprinted with SHA-256 before review admission;
- only Golden Holdout proposals with a dataset fingerprint, sufficient evidence and `candidate_for_human_approval` recommendation can reach `pending_human_approval`;
- proposals that already claim automatic application or production execution are explicitly ineligible;
- review packets persist under `<harness>/policy-review/<packet_id>/review-packet.json` and record proposal id/path/hash, experiment/dataset provenance, source run ids and admission reasons;
- packet ids are immutable once written, preventing silent replacement of the artifact a human is expected to review;
- CLI: `harness-policy-review-packet --kind registration|material_separation|super_resolution --proposal <policy-proposal.json>`;
- API/diagnostics expose the current `human_policy_review_packet_v2`;
- Phase 2R intentionally contains no approve/activate command, never changes `Engine.run_job`, and always keeps `automatically_applied=false` plus `production_execution_enabled=false`.

Phase 2R does not change benchmark recipes because it is a governance/promotion boundary, not a reconstruction or scoring change; recipes remain `local-precision-v22` / `remote-balanced-v22`.

### Phase 2S — Human Decision Receipt Chain

Implemented:

- the immutable review boundary now also accepts Golden SR policy proposals, so registration, material separation and SR use one review mechanism;
- SR proposals with sufficient Golden evidence and a non-`manual_review` recommendation can enter `pending_human_approval`; the packet records cohort/source artifact provenance;
- explicit `approve` or `reject` decisions create a separate immutable receipt bound to the exact review packet SHA-256 and proposal SHA-256;
- receipt creation re-checks that the review packet is pending/eligible and that the underlying proposal file still matches the hash that was reviewed;
- exactly one decision receipt is allowed per review packet; changing a decision requires a new review packet rather than overwriting audit history;
- reviewer identity is mandatory and an optional review note is preserved;
- `approve` only records `human_approval_recorded=true`; it still sets `requires_separate_activation=true`, `automatically_applied=false` and `production_execution_enabled=false`;
- receipts persist under `<harness>/policy-decisions/<packet_id>/decision-receipt.json`;
- CLI: `harness-policy-decision-receipt --packet <review-packet.json> --decision approve|reject --reviewer <name>`;
- API/diagnostics expose `human_policy_review_packet_v2` and `human_policy_decision_receipt_v1`;
- there is still no production activation command and `Engine.run_job` remains unchanged.

Phase 2S is governance-only, so benchmark recipes remain `local-precision-v22` / `remote-balanced-v22`.

### Phase 2T — Production Activation Readiness Dry-Run

Implemented:

- a fail-closed readiness builder starts from a Phase 2S decision receipt and re-verifies the full receipt → review packet → proposal chain;
- readiness requires an explicit human `approve` decision, `human_approval_recorded=true`, reviewer identity, the separate-activation boundary and safe non-production flags;
- the exact review packet and proposal are re-hashed and compared with the hashes frozen into the decision chain;
- packet/proposal ids, policy kind, recommendation, experiment/dataset provenance, Golden tier, dataset fingerprint and sufficient-evidence state must remain mutually consistent;
- rejected decisions, missing artifacts, hash mismatches, invalid typed artifacts, pre-Golden evidence, unsafe flags or recommendation drift produce `blocked`;
- a clean chain produces only `ready_for_explicit_activation`; this is readiness evidence, not activation;
- immutable assessments persist under `<harness>/policy-activation-readiness/<assessment_id>/readiness.json`;
- CLI: `harness-policy-activation-readiness --receipt <decision-receipt.json>`;
- API/diagnostics expose `human_policy_activation_readiness_v1`;
- every assessment keeps `requires_explicit_activation_confirmation=true`, `automatically_applied=false` and `production_execution_enabled=false`;
- Phase 2T adds no activation command and does not change `Engine.run_job`.

The SHA-256 chain is an integrity/audit mechanism for local artifacts; it is not a cryptographic signature of reviewer identity. Phase 2T is governance-only, so benchmark recipes remain `local-precision-v22` / `remote-balanced-v22`.

### Phase 2U — Golden Holdout Execution Preflight

Implemented:

- a read-only fail-closed preflight checks whether the real historical dataset is actually ready before any Golden benchmark/policy chain is started;
- dataset selection is explicit when multiple datasets exist; zero datasets or an unknown dataset id is reported as blocked rather than creating synthetic evidence;
- the registered dataset must be non-empty and expose enough `golden_holdout` members for the configured minimum;
- the immutable dataset manifest must exist, parse successfully, match the registered dataset id and is SHA-256 fingerprinted for downstream provenance;
- every Golden member is checked back to its historical pair plus target/source asset records; missing backing files or SHA-256 drift from the registered asset fingerprint block execution;
- the benchmark recipe is parsed through the strict `BenchmarkRecipe` contract and fingerprinted; the repository local baseline remains `local-precision-v22`;
- registration and material-separation readiness are reported independently from SR readiness;
- SR readiness requires at least one real backend: either a typed local adapter whose executable resolves, or a typed remote adapter plus configured provider recipe with an enabled `super_resolution` action;
- remote provider absence remains a warning for local registration/material benchmarking, but is a blocker for a remote SR lane;
- CLI: `harness-golden-preflight [dataset_id] --recipe <recipe.json> --local-sr-adapter <adapter.json> --remote-sr-adapter <adapter.json>`;
- API/diagnostics expose `golden_holdout_preflight_v1`;
- the preflight writes no benchmark/policy artifacts, never fabricates Golden evidence and always reports `production_execution_enabled=false`.

Phase 2U is readiness-only and does not change `Engine.run_job`, production policy or recipe versions.

Still pending in Phase 1:

- import/run the user's real historical source/final pairs through the benchmark suite and route matrix, then establish the first measured champion/router policy;
- supply the approved Tesseract runtime/language packs for release builds and validate OCR quality on Golden Holdout;
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
