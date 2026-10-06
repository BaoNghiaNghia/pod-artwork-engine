# POD Artwork Reconstruction Tool

> **Status:** Canonical design document — standalone-tool architecture
> **Role:** Source of truth for POD artwork reconstruction, quality, learning, harness, logging and update decisions.

## 1. Product definition

The product is a **standalone desktop tool** that runs on the user's local machine:

```text
Open POD Artwork Tool
        ↓
Drag/drop or upload one or more reference images
        ↓
Tool analyzes and reconstructs the artwork
        ↓
Tool performs quality checks and targeted repair
        ↓
User reviews the result
        ↓
Export clean 2D artwork
```

The primary user experience is intentionally simple: **Input → Process → Output**.

Internally the tool may use local GPU models, deterministic image processing and remote AI providers, but those implementation details should normally remain hidden from the user.

The primary output is a clean 2D artwork master suitable for POD production. A 4500×5400 RGBA PNG with 300-DPI metadata is the default delivery profile, but print quality is determined by actual detail, text, edges, alpha and fidelity rather than file dimensions alone.

## 2. Product UX

Main screen:

```text
┌────────────────────────────────────────┐
│        POD Artwork Reconstruction      │
├────────────────────────────────────────┤
│                                        │
│   Drag images here                     │
│   or [ Select Images ]                 │
│                                        │
├────────────────────────────────────────┤
│ Mode                                   │
│ ○ Quick 2D                             │
│ ● Print Ready                          │
│ ○ Max Fidelity                         │
├────────────────────────────────────────┤
│ Processing                             │
│ [██████████████░░░░] 74%               │
│ Rebuilding artwork texture             │
├────────────────────────────────────────┤
│ Input                  Output          │
│ [ preview ]            [ preview ]     │
├────────────────────────────────────────┤
│ [Regenerate] [Enhance] [Export]        │
└────────────────────────────────────────┘
```

The main workflow must not expose queue internals, model names, worker topology, harness configuration or learning internals unless the user opens Advanced/Developer views.

### 2.1 Quality modes

- **Quick 2D:** prioritize turnaround time; produce a reviewable clean 2D draft quickly.
- **Print Ready:** default mode; use smart routing, local precision finish and full QC.
- **Max Fidelity:** use stronger analysis, alternate candidates where justified, targeted rescue and stricter QC.

A Quick 2D result can be upgraded to Print Ready or Max Fidelity from cached/checkpointed artifacts without repeating valid earlier stages.

## 3. Canonical system architecture

The desktop application is one product but internally separates UI and processing so heavy GPU work or model failure does not freeze the interface.

```text
                 PODArtworkTool.exe
                         │
              ┌──────────┴──────────┐
              │                     │
         Desktop UI            Engine Process
              │                     │
              │            ┌────────┼─────────┐
              │            ▼        ▼         ▼
              │       Deterministic Local AI Remote AI
              │          stages     models    providers
              │            │        │         │
              │            └────────┼─────────┘
              │                     ▼
              │                Reconstruction
              │                     ▼
              │               Precision Finish
              │                     ▼
              │                    QC
              │                     ▼
              └────────────────── Output
```

If the engine process fails, the UI remains alive, the failure is logged and the job resumes from the latest valid checkpoint when possible.

## 4. Canonical processing workflow

```text
INPUT / REFERENCE SET
        │
        ▼
PRE-FLIGHT
        │
        ├─ hash / deduplicate
        ├─ source quality
        ├─ dimensions / orientation
        ├─ blur / compression
        ├─ artwork detection
        └─ metadata / provenance
        │
        ▼
ARTWORK IDENTITY
        │
        ▼
MULTI-REFERENCE FUSION
(if several images show the same artwork)
        │
        ▼
LIGHT ANALYZER
        │
        ├─ DesignSpec
        ├─ OCR/text
        ├─ composition
        ├─ material/texture
        ├─ warp/occlusion
        └─ confidence
        │
        ▼
TYPED VALIDATION
        │
        ▼
SMART ROUTER
   ┌────┼───────────┐
   ▼    ▼           ▼
DETERMINISTIC    LOCAL AI    REMOTE AI
text/logo/etc.   Qwen/FLUX*  fast/precision*
   │              │           │
   └──────────────┼───────────┘
                  ▼
           SEMANTIC MASTER
                  │
             QC GATE #1
           source fidelity
                  │
                  ▼
          PRECISION FINISH
   vector / raster / texture / SR
         alpha / edge / compositor
                  │
                  ▼
             QC GATE #2
          technical print quality
                  │
          ┌───────┼───────────┐
          ▼       ▼           ▼
        PASS   REGION       REVIEW
               RESCUE
          │       │           │
          └───────┴───────────┘
                  ▼
              FINAL MASTER
                  │
             USER FEEDBACK
                  │
                  ▼
            EXPERIENCE STORE
                  │
             LEARNING LOOP
```

`*` Concrete provider/model names are provider mappings selected by benchmark and are not permanent architecture.

## 5. Pre-flight and source quality

Pre-flight runs before expensive AI work.

It should determine:

- file hash and duplicate status;
- dimensions and format;
- source quality;
- blur/compression severity;
- transparency if present;
- orientation;
- likely artwork region;
- whether the input belongs to an existing artwork identity;
- quality mode, priority and optional budget constraints.

If an identical or equivalent artwork already has a valid master, the system may reuse it instead of repeating reconstruction.

## 6. Artwork Identity and multi-reference fusion

One artwork may have multiple references:

```text
artwork_001/
  front.jpg
  closeup.jpg
  model.jpg
  side.jpg
```

All references should map to one `artwork_identity`.

The system should use multiple references when available to reduce hallucination and recover information hidden by perspective, wrinkles, hands, folds or poor framing.

Multi-reference fusion may:

- select the clearest region from each image;
- estimate perspective/dewarp transforms;
- build a shared evidence map;
- identify mutually consistent details;
- mark unresolved regions as low confidence.

Do not force one arbitrarily selected image to be the sole source when better evidence exists across several references.

## 7. DesignSpec and typed control plane

The analyzer creates a structured `DesignSpec` containing, where available:

- artwork bounding region;
- exact recognized text and line order;
- objects and relative positions;
- layout/composition;
- dominant colors;
- style/texture classes;
- gradient/metallic/distress/watercolor/glow indicators;
- perspective/warp severity;
- occlusion;
- confidence by region/element;
- preservation constraints;
- recommended capabilities required.

Use a strict typed/validated control plane such as JEV TypeSafe or an equivalent implementation for:

```text
DesignSpec
RouteDecision
ProviderRequest
ProviderResult
JudgeResult
QCResult
RetryDecision
ArtifactManifest
ExportProfile
UserFeedback
JobExperience
LearningRecommendation
```

All contracts are versioned.

Invalid structured output must not silently continue. The system should attempt bounded deterministic repair, structured retry, fallback or manual review.

AI models provide observations; validated policy code decides routing, retries, fallback and escalation unless a learned policy has explicitly passed promotion gates.

## 8. Smart Router

Routing is capability-based, not hard-coded around model names.

Example capabilities:

```text
need_fast_draft
need_semantic_reconstruction
need_exact_text
need_complex_texture
need_region_rescue
need_vector
need_high_resolution
need_fallback
```

Typical routes:

### 8.1 Deterministic route

Preferred for text/logo/geometric work when reliable reconstruction is possible:

```text
OCR
→ exact text verification
→ font/layout/style analysis
→ vector/shape rebuild
→ high-resolution render
```

### 8.2 Local AI route

Used for illustration, material separation, semantic reconstruction, SR or other operations where a benchmarked local model performs well.

### 8.3 Remote AI route

Used for:

- Quick 2D;
- difficult semantic reconstruction;
- severe deformation/occlusion;
- local-model failure;
- region rescue;
- fallback when local compute is unavailable.

### 8.4 Hybrid route

Use AI for semantic recovery and deterministic/local precision processing for exact text, vectors, gradients, texture, SR, alpha, edge and final composition.

## 9. Reconstruction quality principles

### 9.1 Text and logo

Final glyphs should not depend on a generative image model when deterministic reconstruction is possible.

Any text mismatch is a QC failure.

### 9.2 Illustration

Preserve intentional visual texture while removing garment/fabric effects.

### 9.3 Mixed artwork

Separate typography/vector-capable elements from illustration/texture and composite them after reconstruction.

### 9.4 Material separation

Observed pixels may contain:

```text
Artwork Base              → keep/reconstruct
Artwork Texture           → keep/reconstruct
Garment/Fabric Texture    → remove
Lighting/Shadow           → remove
Wrinkle/Geometry          → correct
Compression/Noise         → reduce/remove
```

The core challenge is distinguishing intentional artwork texture from product texture.

## 10. Difficult gradients and textures

- **Smooth gradients:** use vector/procedural gradients where fit is reliable.
- **Metallic/gold/specular:** use sharp masks plus high-resolution raster/procedural texture and highlight information.
- **Watercolor/distressed/grunge:** preserve as high-resolution raster texture/masks.
- **Glow/semi-transparency:** retain high-precision alpha handling.
- **Texture synthesis:** allowed only when source detail is insufficient; generated detail must be treated as plausible synthesis, not recovered ground truth.

## 11. Artifact DAG, checkpoints and cache

The processing pipeline is a dependency graph rather than a monolithic script.

Each stage should be reproducible from:

```text
input artifact hash
+
configuration hash
+
model/provider version
+
prompt/policy/schema version
```

Each successful stage emits an immutable artifact record.

Benefits:

- resume after crash;
- avoid repeating expensive AI calls;
- upgrade Quick 2D to Print Ready without restarting;
- rerun only SR/alpha/export when upstream artifacts remain valid;
- reuse identical preprocessing across benchmark recipes;
- support reproducible debugging.

A stage must be idempotent where practical.

## 12. Master artifact representation

A design may retain:

```text
master/
  source/
  preflight.json
  design_spec.json
  ocr.json
  confidence_map
  evidence_map
  vector/
    typography
    line_art
    geometry
    gradients
  raster/
    illustration
    watercolor
    distress
    metallic/detail textures
  masks/
    alpha
    texture
    edge
  candidates/
  qc/
  artifact_manifest.json
  final_master
```

Internally use higher precision and a working resolution above delivery size where beneficial.

## 13. Two-stage quality control

Do not collapse all quality checks into one score.

### 13.1 QC Gate #1 — Semantic/source fidelity

Checks:

- exact text;
- layout/composition;
- object presence and shape;
- color;
- missing regions;
- source similarity;
- intentional texture;
- unresolved/occluded areas.

### 13.2 QC Gate #2 — Technical print quality

Checks:

- effective resolution;
- edge sharpness;
- aliasing;
- halo;
- alpha;
- blur;
- line survival;
- transparent-pixel contamination;
- output dimensions/profile;
- color-space/export validity.

A candidate must pass both gates for Print Ready.

AI Judge is only one evaluator. Final QC may combine OCR, CV/perceptual metrics, edge metrics, alpha metrics, technical validation and human review.

## 14. Region rescue and bounded retries

If only part of a design fails, do not regenerate the entire image by default.

```text
candidate
   ↓
failed region detected
   ↓
crop + context + mask
   ↓
local or remote rescue
   ↓
merge into locked good regions
   ↓
re-run targeted QC
```

Retries are bounded.

Failure type determines the action:

```text
TEXT_FIDELITY_FAILED  → deterministic text repair
TEXTURE_FAILED        → texture/local/remote region rescue
ALPHA_FAILED          → alpha stage rerun
PROVIDER_ERROR        → provider fallback
OUT_OF_MEMORY         → lower-memory route / alternate worker/provider
SOURCE_INSUFFICIENT   → manual review / alternate reference
```

## 15. Export profiles

Do not hard-code the entire architecture to one output size.

Default POD profile:

- PNG;
- 4500×5400;
- RGBA;
- transparent background;
- 300-DPI metadata.

An `ExportProfile` may additionally define:

- canvas dimensions;
- aspect behavior;
- color-space/ICC policy;
- transparency rules;
- safe margins;
- scaling;
- output formats.

Possible presets include Default POD, Printify, Printful and Custom.

The internal master is independent from delivery size so future exports do not require full reconstruction.

## 16. Job lifecycle and error taxonomy

Core states may include:

```text
queued
preflight
analyzing
dewarping
separating
reconstructing
judging
precision_finishing
upscaling
alpha_refining
qc
region_rescue
retrying
review_required
completed
failed_retryable
failed_final
cancel_requested
cancelled
resuming
waiting_provider
waiting_compute
blocked_budget
```

Use normalized failure categories such as:

```text
SOURCE_ERROR
PROVIDER_ERROR
COMPUTE_ERROR
QUALITY_FAILURE
POLICY_FAILURE
BUDGET_FAILURE
CANCELLED
```

This allows deterministic retry/fallback policy.

## 17. Logger, telemetry and diagnostics

Logging is mandatory from the first build.

Separate:

- **Operational logs:** app/engine/update/debug/errors.
- **Telemetry:** latency, resource use, retries, provider/model/version, quality metrics and cost where applicable.
- **Experience Store:** normalized long-lived learning evidence.

Suggested local structure:

```text
PODTool/
  logs/
    app.log
    engine.log
    updater.log
    jobs/
      <job_id>.jsonl
```

Each stage records at least:

- timestamp;
- job/trace ID;
- stage;
- provider/model/version;
- prompt/policy/schema version;
- attempt;
- duration;
- input/output artifact IDs;
- retry/failure reason;
- GPU/VRAM/RAM metrics where available;
- cost where applicable.

Logging must not block image processing.

### 17.1 Diagnostic bundle

The tool provides **Export Diagnostic Bundle**:

```text
diagnostic_<timestamp>.zip
  system.json
  gpu.json
  app-version.json
  updater.log
  recent-job-logs/
  health-report.json
  config-redacted.json
```

Never include secrets, credentials or API keys.

## 18. Auto-update and rollback

Auto-update is part of the foundation, not a later add-on.

Startup:

```text
Launch tool
    ↓
Updater bootstrap
    ↓
Check release manifest
    ↓
No update ─────────→ Start installed version
    ↓
Update available
    ↓
Download package
    ↓
Verify checksum/signature
    ↓
Backup current version
    ↓
Install
    ↓
Start + health check
    ├─ PASS → keep new version
    └─ FAIL → rollback automatically
```

If the update service or internet is unavailable, log a warning and start the installed version.

Support at least:

```text
stable
staging
```

The updater should be independent from the main engine so a broken release cannot prevent rollback.

**Implemented foundation status:** the Windows launcher is now an independent bootstrap executable. It keeps versioned releases under the managed update directory, stages checksum-verified packages without replacing the active release, launches the staged desktop/engine pair, validates the expected engine version through `/health`, activates only after a successful health gate, and automatically falls back to the previous known-good release when the staged or active release fails. Update cleanup explicitly protects the active, previous and staged release directories. Remote manifest hosting and code-signing policy remain deployment configuration.

## 19. Historical learning from existing input/final pairs

Existing historical data is a first-class starting asset.

```text
Historical Inputs
       +
Approved Finals
       ↓
Pairing
       ↓
Artwork Identity
       ↓
Normalization
       ↓
Historical Dataset
       ↓
Experience / Retrieval / Benchmark / Training
```

A historical record should link one or more product/reference images to one approved final artwork.

The system should support import from folders, filenames, SKU/design IDs or visual matching.

Do not immediately fine-tune on all historical data. Use it first for:

1. similar-case retrieval;
2. recipe learning;
3. router/provider evaluation;
4. QC/ranker development;
5. material-separation evaluation;
6. only later, justified local-model adaptation/fine-tuning.

## 20. Synthetic paired data

Approved clean masters can produce synthetic degraded references:

```text
clean master
   ↓
mockup simulator
   ├─ perspective
   ├─ wrinkle
   ├─ lighting
   ├─ garment texture
   ├─ blur/compression
   └─ partial occlusion
   ↓
synthetic product reference
```

Because the clean master is known, the pair becomes controlled training/evaluation data.

Synthetic data supplements real historical pairs; it must not replace real validation.

## 21. Dataset Registry and split rules

All datasets are versioned.

Never randomly split individual reference images if they show the same artwork.

Split by `artwork_identity` so one design cannot leak across train/validation/test.

A typical starting split may be:

```text
Train            75%
Validation       10%
Golden Holdout   15%
```

The Golden Holdout is never used for:

- training;
- prompt learning;
- router learning;
- retrieval memory.

It exists only for unbiased regression and promotion decisions.

## 22. Experience Store and continuous learning

Every eligible completed job can add evidence:

```text
input
DesignSpec
route
provider/model
candidate
QC
retry history
user feedback
final selected result
cost/latency/resource metrics
```

Learning occurs in layers:

1. **Experience retrieval:** use similar successful/failed cases.
2. **Recipe learning:** learn the best processing recipe for each artwork family.
3. **Policy learning:** improve route/provider/retry decisions.
4. **Prompt/parameter learning:** compare versioned alternatives.
5. **Model learning:** fine-tune/LoRA only after clean dataset evidence justifies it.

User feedback should support:

```text
Approve
Reject
Regenerate
Corrected
Final selected
```

Structured reasons may include wrong text/layout/object/color, lost texture, fake texture, blur, bad edges, bad alpha or excessive source deviation.

Corrected results create high-value preference pairs:

```text
undesired candidate → approved correction
```

## 23. Reconstruction Recipe memory

The system learns complete recipes rather than only “best model”.

Example:

```text
Artwork family: metallic logo

Best recipe:
vector extraction
→ precise geometry
→ metallic texture mask
→ alpha cleanup
→ high-resolution render
```

Another example:

```text
Artwork family: watercolor + typography

Best recipe:
semantic reconstruction
→ exact OCR/text rebuild
→ preserve raster watercolor
→ local SR
→ alpha refinement
```

Recipe quality is measured by the Harness.

## 24. Benchmark / Evaluation Harness

The Harness is a first-class subsystem, not a temporary script.

It does **not** process user production jobs directly. It measures whether a proposed change is better than the production champion.

```text
Dataset Registry
      ↓
Dataset Snapshot
      ↓
Benchmark Plan
      ↓
Recipe A / B / C
      ↓
Normalized Results
      ↓
Semantic QC
Technical QC
Operational Metrics
      ↓
Scorecard
      ↓
Visual Diff
      ↓
Human Spot Check
      ↓
Champion / Challenger
      ↓
Promotion Gate
```

### 24.1 Benchmark case

Each benchmark case contains:

```text
case_id
artwork_identity
source reference set
approved target/master
metadata
exact text/constraints
difficulty/cohort labels
```

### 24.2 Benchmark recipes

The Harness compares **whole recipes**, not just models.

A recipe versions analyzer, dewarp, reconstruction, OCR, vectorization, SR, alpha, QC, prompt and routing settings.

### 24.3 Metrics

Semantic:

- exact text;
- composition/layout;
- object fidelity;
- color;
- texture fidelity;
- missing detail.

Technical:

- edge;
- alpha;
- halo/aliasing;
- blur;
- effective resolution;
- small-detail survival.

Operational:

- latency;
- GPU time;
- peak VRAM/RAM;
- retries;
- provider calls;
- cost;
- failure/manual-review rate.

### 24.4 Cohort evaluation

Results must be broken down by artwork category, for example:

```text
typography
logo
mixed
watercolor
metallic
distressed
flat illustration
heavy wrinkle
low resolution
heavy occlusion
```

Do not promote a change from average score alone if it causes a serious cohort regression.

### 24.5 Regression tiers

```text
Smoke Set
   ↓
Regression Set
   ↓
Golden Holdout
   ↓
Promotion Gate
```

### 24.6 Visual Diff

Harness UI should provide source/target/result comparison, synchronized 100/200/400% zoom, difference heatmaps and targeted crops for text, fine lines, gradient, texture and alpha edge.

### 24.7 Shadow evaluation

Production continues using the Champion while a Challenger may run on selected jobs without affecting user output.

No router/model/prompt/QC policy may self-promote based solely on AI Judge results.

## 25. Champion / Challenger promotion

Version:

- router;
- prompts;
- provider mapping;
- QC thresholds;
- recipes;
- model settings.

A challenger is promoted only after measured evidence shows acceptable quality and no critical regression, while cost/latency/resource use satisfies policy.

Production behavior must never mutate automatically from a few recent jobs.

## 26. Reproducibility and security

Every final master has an `ArtifactManifest` containing:

- source hashes;
- parent artifact hashes;
- model/provider versions;
- prompt/policy/schema versions;
- parameters and seed where supported;
- final export profile;
- QC report.

Text extracted from images is treated as **data**, never as executable model instructions.

Remote providers receive only required artifacts. Credentials are stored securely and never written to logs, diagnostics or datasets.

## 27. Standalone local deployment

The local Windows machine is the primary runtime and the architecture is optimized for the current hardware profile:

```text
CPU        Intel Xeon E5-2680 v4
Sockets    2
Cores      28 physical / 56 logical
RAM        64 GB
GPU        Radeon RX 470 8 GB
Storage    SSD
```

This machine is strong for highly parallel CPU work and large in-memory image processing, but the RX 470 8 GB must **not** be treated as the primary engine for modern large image-generation models. The production default is therefore **CPU-first + lightweight GPU acceleration + remote AI for semantic reconstruction**.

Suggested product components:

```text
PODArtworkTool.exe
  ├─ Desktop UI
  ├─ Engine Service/Process
  ├─ CPU image-processing runtime
  ├─ Lightweight GPU acceleration layer
  ├─ Remote AI provider adapters
  ├─ JEV/typed control plane
  ├─ Logger + Telemetry
  ├─ Auto Updater
  ├─ Checkpoint/cache manager
  ├─ Storage Manager
  └─ Dataset/Harness management
```

The tool should not require users to manually start Python or PowerShell in normal use.

### 27.1 Canonical execution policy for the current machine

Default routing:

```text
INPUT
  ↓
CPU PRE-FLIGHT
hash / quality / crop / OCR
  ↓
TYPED VALIDATION
  ↓
SMART ROUTER
  ├─ CPU deterministic path
  │    OCR / OpenCV / dewarp / vector / compositor / QC
  │
  ├─ lightweight RX470 path
  │    only stages that benchmark faster through DirectML/Vulkan-class acceleration
  │
  └─ remote AI path
       semantic analysis
       difficult reconstruction
       Quick 2D
       region rescue
       fallback
  ↓
LOCAL PRECISION FINISH
  ↓
QC #1 + QC #2
  ↓
OUTPUT
```

Do not make local Qwen/FLUX-class large reconstruction models a required production dependency on this hardware. They may be benchmarked experimentally, but the default install must remain usable without them.

### 27.2 CPU utilization policy

The engine should exploit the 28-core/56-thread CPU without starving Windows or the desktop UI.

Recommended initial scheduler limits:

```text
Engine CPU soft budget     32–40 logical threads
UI/system reserve          remaining threads
RAM soft budget            ~32 GB
RAM hard budget            44–48 GB
```

Typical CPU responsibilities:

- OCR;
- pre-flight and deduplication;
- OpenCV transforms;
- perspective/dewarp;
- vector/text reconstruction;
- image compositing;
- masks and alpha processing where CPU wins;
- QC and benchmark metrics;
- dataset indexing and background preparation.

Independent CPU work should overlap remote-provider wait time. For example, OCR, masks, layout reconstruction and output-canvas preparation can run while a remote reconstruction request is in flight.

### 27.3 RX 470 policy

The RX 470 is an optional accelerator, not a mandatory dependency.

Benchmark lightweight tasks through available Windows acceleration backends such as DirectML/Vulkan-class paths where practical:

- lightweight segmentation/matting;
- selected SR models;
- image filters;
- narrowly scoped inference that fits comfortably in 8 GB VRAM.

If the GPU implementation is unstable or slower than CPU, the Router must automatically use the CPU path.

Do not make the following normal production requirements on this GPU:

```text
large local diffusion reconstruction
large local VLM
multiple resident AI models
VRAM-heavy Max Fidelity pipelines
```

### 27.4 Remote AI policy

Remote AI is used as elastic compute to avoid turning the RX 470 into the system bottleneck.

Use remote reasoning/vision for:

- DesignSpec generation when local evidence is insufficient;
- ambiguous or heavily occluded artwork;
- multi-reference reasoning;
- difficult semantic understanding;
- Judge assistance where deterministic metrics are insufficient.

Use remote image reconstruction for:

- Quick 2D;
- complex semantic reconstruction;
- difficult-region rescue;
- fallback after bounded local failure;
- Max Fidelity paths when evidence shows quality benefit.

Remote output is still an intermediate reconstruction. Final exact text, vector-capable geometry, alpha, edges, export profile and technical QC remain controlled by the local precision pipeline.

### 27.5 JEV / typed-control optimization

JEV/TypeSafe does not make image inference faster directly. Its performance value is preventing invalid structured output and bad routing decisions from wasting expensive stages.

The typed control plane validates:

```text
DesignSpec
RouteDecision
ProviderResult
QCResult
RetryDecision
ArtifactManifest
```

A malformed or incomplete result should fail within milliseconds/seconds at the contract boundary instead of continuing through reconstruction, SR, compositing and QC.

### 27.6 Hard storage budget

The entire installed tool footprint must stay within a **40 GB absolute cap**, excluding user-controlled historical source/final datasets stored outside the tool directory.

Target normal footprint:

```text
Core app/runtime               2–3 GB
OCR/matting/SR lightweight     2–4 GB
Pinned production models       0–6 GB
Persistent cache               ≤5 GB
Temporary jobs                 ≤6 GB
Harness/Golden subset          ≤2 GB
Logs                           ≤1 GB
Updater/rollback               ≤2 GB
Reserved headroom              ≥4 GB
--------------------------------------
Normal operating target        ~18–28 GB
Soft warning                   32 GB
Absolute hard limit            40 GB
```

Storage Manager automatically:

1. removes completed-job temporary artifacts no longer needed for resume;
2. evicts unpinned LRU model/cache data;
3. compresses/rotates logs;
4. deletes superseded update packages;
5. refuses optional model downloads that would exceed the hard cap;
6. uses a remote provider instead of downloading another large fallback model when appropriate.

Historical datasets are referenced by path/hash/metadata and are not copied into the tool installation by default.

### 27.7 Performance targets for this hardware profile

Initial engineering targets, to be replaced by Harness measurements:

```text
Typography/logo deterministic    8–20 s
Quick 2D                         15–45 s
Print Ready simple               20–45 s
Print Ready typical              30–90 s
Complex hybrid                   1–2 min
Region rescue                    +15–45 s
Max Fidelity                     1.5–4 min typical
Hard job ceiling                 6–8 min
```

These are SLO targets, not guaranteed benchmark results. Actual values depend on remote-provider latency, network, source complexity and the final selected algorithms.

The engine should stop unproductive retries before the hard ceiling and preserve artifacts for manual review or fallback.

## 28. Optional VPS role

A VPS is **optional**, not required for the basic standalone workflow.

Possible VPS responsibilities:

- update/release manifest hosting;
- optional telemetry aggregation;
- optional remote job history;
- optional centralized dataset registry;
- remote provider orchestration;
- multi-machine expansion later.

The standalone app must still process local jobs when the VPS is unavailable, except for features that explicitly require remote providers or update/telemetry services.

## 29. Recommended source structure

```text
pod-artwork-tool/
│
├─ desktop/
│  ├─ ui/
│  └─ updater/
│
├─ engine/
│  ├─ preflight/
│  ├─ identity/
│  ├─ fusion/
│  ├─ analyzer/
│  ├─ pipeline/
│  ├─ router/
│  ├─ providers/
│  ├─ vector/
│  ├─ texture/
│  ├─ sr/
│  ├─ alpha/
│  ├─ compositor/
│  ├─ qc/
│  ├─ artifacts/
│  └─ checkpoints/
│
├─ contracts/
│
├─ learning/
│  ├─ experience_store/
│  ├─ retrieval/
│  ├─ recipe_learning/
│  └─ dataset_registry/
│
├─ harness/
│  ├─ datasets/
│  ├─ recipes/
│  ├─ runner/
│  ├─ evaluators/
│  ├─ visual_diff/
│  └─ scorecards/
│
├─ telemetry/
│  ├─ logger/
│  ├─ metrics/
│  ├─ traces/
│  └─ diagnostics/
│
├─ data/
│  ├─ models/
│  ├─ cache/
│  ├─ jobs/
│  └─ datasets/
│
├─ scripts/
│
└─ docs/
```

The architecture should keep provider adapters replaceable so model changes do not rewrite the core application.

## 30. Implementation roadmap

### Phase 0A — Standalone foundation

**Status: implemented and locally verified.** Release-manifest hosting/code-signing remain deployment configuration; the local runtime, bootstrap activation, health gate and automatic rollback are in place.

Build:

- desktop shell;
- separate engine process;
- job IDs/state machine;
- typed contracts;
- artifact manifest;
- structured logger;
- diagnostic bundle;
- auto-update + rollback;
- checkpoint/cache foundation.

### Phase 0B — Historical data foundation

**Status: implemented and locally verified.** The engine now imports historical source/final pairs without copying the original image corpus, assigns stable artwork identities, deduplicates exact sources and conservative visual-equivalent targets, writes versioned Dataset Registry snapshots, and splits strictly by `artwork_identity`. Golden Holdout members are marked non-retrieval-eligible so later learning/retrieval code cannot consume them accidentally.

Implemented foundation:

- folder importer with filename-based grouping for one-to-many reference sets;
- optional SKU/design-ID extraction by regex;
- explicit JSON pairing manifests for cases that filenames cannot resolve;
- exact SHA-256 source deduplication and alias-location tracking;
- normalized + conservative perceptual target matching for artwork identity;
- SQLite historical asset/pair/artwork registry;
- immutable dataset manifest snapshots referencing original paths/hashes;
- deterministic train / validation / Golden Holdout allocation at artwork level;
- persistent split assignment per `artwork_identity`, so an existing Golden identity cannot silently move into train in a later dataset version;
- dataset/historical-pair read APIs and CLI inspection.

The visual fallback is intentionally conservative. It is suitable for near-equivalent clean artwork/reference images, not for assuming that a garment mockup and a clean master are the same design. Semantic source→target visual pairing remains a later analyzer capability.

Build:

- historical input/final importer;
- pairing;
- artwork identity;
- deduplication;
- Dataset Registry;
- Golden Holdout.

### Phase 0C — Harness foundation

**Status: implemented and locally verified at the backend foundation level.** The Harness now consumes immutable Dataset Registry snapshots, materializes typed benchmark cases by tier, evaluates normalized candidate outputs, writes per-case metrics/visual diffs, aggregates scorecards by cohort and applies a Golden promotion guard. Direct execution of reconstruction recipes will plug into the same runner once Phase 1 provider/pipeline adapters exist; until then the runner evaluates candidate manifests produced by experiments or external providers.

Implemented foundation:

- typed `BenchmarkCase`, `BenchmarkPlan`, `BenchmarkRecipe`, metric, result, scorecard and promotion contracts;
- deterministic Smoke cases from Train, Regression cases from Validation and Golden cases from Golden Holdout;
- strict exact-text scoring when recognized/OCR text is supplied;
- deterministic layout/color/texture-detail proxy metrics;
- technical edge/alpha/halo/blur/effective-resolution/small-detail metrics;
- operational latency/GPU/VRAM/RAM/retry/provider-call/cost/manual-review metrics;
- metric-coverage reporting so unavailable semantic metrics are never silently treated as perfect;
- persisted per-case result JSON plus target/result/diff preview artifacts;
- scorecards with global and cohort-level quality/failure summaries;
- Champion/Challenger comparison that rejects serious cohort regressions even when the global average improves;
- Golden-only promotion by default, with no automatic self-promotion;
- read-only Harness API plus CLI inspection/run/compare commands;
- Harness storage included in the global tool budget with a 2 GB quota and whole-run eviction.

Important limitation: `object_fidelity` remains unset until a semantic evaluator is connected. The current deterministic image metrics are engineering proxies, not substitutes for a VLM/Judge or human review. The interactive synchronized-zoom Harness UI described in §24.6 is a later UI layer; Phase 0C establishes the data, scorecards and visual-diff artifacts it will consume.

Build:

- benchmark case schema;
- recipe schema;
- runner;
- semantic/technical/operational metrics;
- scorecards;
- visual diff;
- smoke/regression/golden tiers.

### Phase 1 — Reconstruction V1

**Status: Phase 1A implemented; Phase 1B text-fidelity/Judge foundation implemented; Phase 1C deterministic precision foundation implemented and locally verified.** The tool now executes an end-to-end reconstruction pipeline from uploaded references to a 4500×5400 RGBA PNG instead of stopping after pre-flight. The operational pipeline is separated from provider selection and deterministic precision stages.

Implemented in Phase 1A:

- enhanced pre-flight with hashes, dimensions, EXIF orientation, sharpness/compression triage, source-quality score and artwork-region detection;
- local `DesignSpec` analyzer for bbox, dominant colors, coarse artwork family, texture class, confidence and required capabilities;
- strict typed contracts for `RouteDecision`, `ProviderRequest`, `ProviderResult`, `QCResult`, `ExportProfile` and resource telemetry;
- equivalent JEV/TypeSafe boundary behavior using strict versioned Pydantic contracts plus bounded deterministic JSON extraction/repair;
- capability router supporting deterministic, remote-semantic and hybrid routes;
- vendor-neutral remote semantic gateway so GPT-6-family support, another remote model or an internal gateway can be mapped without changing core pipeline code;
- Quick 2D remote-first behavior when a provider gateway is configured;
- invalid/failed provider results fall back to a deterministic local baseline and are logged;
- provider requests do not expose local filesystem paths; normalized reference images are transferred instead;
- local clean/flat-source reconstruction baseline with crop, alpha preservation or conservative border-color soft masking;
- precision normalization and transparent 4500×5400 PNG export with 300-DPI metadata;
- QC Gate #1 for semantic/source-confidence policy and exact-text evidence when supplied;
- QC Gate #2 for dimensions, alpha, effective source resolution and edge/detail checks;
- per-stage checkpoints and restart/resume through the full reconstruction pipeline;
- final `ArtifactManifest` with source hashes, output hashes, provider/model metadata and both QC reports;
- stage-level CPU/RAM/storage telemetry in structured logs;
- Desktop preview plus local `/jobs/{job_id}/output` export endpoint.

Remote gateway configuration:

```text
POD_REMOTE_PROVIDER_URL=
POD_REMOTE_PROVIDER_TOKEN=
POD_REMOTE_PROVIDER_NAME=remote
POD_REMOTE_PROVIDER_TIMEOUT_SECONDS=120
```

The gateway receives versioned typed `analyze`, `reconstruct` and policy-controlled `judge` requests. Provider responses must pass strict typed validation before the pipeline consumes them. Local filesystem paths and secrets are not sent as provider metadata; source/candidate images are normalized for transfer and credentials remain local.

Phase 1B text-fidelity/Judge foundation:

- provider OCR evidence is merged into `DesignSpec.exact_text`;
- `TypographySpec` records ordered text lines, normalized boxes, font-family/weight hints, fill/stroke, rotation and per-line confidence;
- deterministic typography rendering is allowed only when line-order/font/line confidence passes policy thresholds;
- the deterministic renderer resolves only an explicitly matched installed/local font and refuses silent substitution;
- user-supplied local fonts can be placed under `<data-root>/fonts`;
- difficult Print Ready and Max Fidelity jobs can run a separate semantic `judge` action against SOURCE + CANDIDATE + DesignSpec;
- Judge output is strictly typed and reports exact-text/layout/object-fidelity/color/texture/missing-detail evidence;
- policy code, not the Judge itself, applies pass/fail thresholds;
- exact text remains a hard Print Ready / Max Fidelity gate;
- Harness candidate manifests can carry semantic Judge evidence so `semantic.object_fidelity` becomes measurable and its coverage is visible in scorecards.

Phase 1C deterministic precision foundation:

- provider actions are a typed enum (`analyze`, `reconstruct`, `judge`) rather than an open string contract;
- typography evidence carries provider/version provenance so OCR/font-identification backends remain replaceable;
- mixed artwork supports deterministic text replacement only for explicitly approved `replace_solid` regions with a known replacement fill;
- replacement is region-bounded: pixels outside those approved text boxes are preserved exactly by the deterministic stage;
- lines without safe replacement evidence are skipped rather than erased or guessed;
- deterministic typography/geometry failures can escalate to the configured semantic provider;
- logo geometry uses typed high-confidence rectangle, ellipse, line and polygon primitives;
- geometry can be rendered to a high-resolution raster master and retained as a reusable SVG geometry artifact;
- low-confidence or structurally incomplete geometry is rejected instead of being presented as recovered vector truth.

Phase 1D local text, Bezier and production-Harness foundation:

- optional Tesseract adapter provides a concrete local OCR path without introducing a large always-resident OCR model;
- OCR is capability-gated: the engine runs it only for likely typography/logo/mixed artwork and only when the executable is actually available;
- confidence-filtered OCR output becomes versioned `TypographySpec` evidence with normalized line boxes and exact-text candidates;
- OCR evidence remains data, never instructions; it can be supplied to provider analysis/QC but does not bypass typed validation or policy gates;
- installed and user-local fonts are indexed by actual family/style metadata, with canonical same-family alias normalization and strict weight matching;
- `<data-root>/font_aliases.json` can extend normalization without modifying engine source;
- provider font hints can be normalized to an actually installed family, but unresolved/weight-mismatched fonts remain unresolved instead of silently substituting;
- geometry adds a typed cubic-Bezier path representation; SVG retains exact control points while deterministic raster output uses a bounded sampled approximation;
- deterministic path rendering rejects unsupported multi-subpath/structurally ambiguous geometry instead of pretending to recover it exactly;
- mixed-art text cleanup adds explicit polygon replacement masks; every mask is constrained to a guarded expansion of its approved text bbox;
- arbitrary illustration-aware inpainting is still delegated to a semantic/region-rescue provider rather than faked deterministically;
- real `HarnessEngineRunner` executes the actual production engine against versioned Dataset Registry cases and then reuses the same scorecard/evaluator path;
- benchmark recipes can override provider-recipe path and local-OCR policy, so local, GPT-gateway and other challenger recipes can be measured with the same dataset/tier;
- Harness scorecards now record precision-path coverage (`local_ocr`, `typography_rebuilt`, `mixed_text_refined`, `geometry_vector`, masked-region use and provider-recipe use).

Example real-engine benchmark:

```powershell
python -m pod_artwork_engine harness-engine-run historical-v1 --tier smoke --recipe config/benchmark-recipe.local.json --quality-mode print_ready --limit 8
```

`config/benchmark-recipe.remote.example.json` demonstrates a remote challenger whose provider recipe is resolved relative to the benchmark recipe. Provider URL/token remain local environment configuration. This intentionally separates benchmark recipe identity from secrets and from concrete provider credentials.

Phase 1E benchmark-suite and safe policy-calibration foundation:

- `BenchmarkSuiteRunner` evaluates one champion against one or more challengers in strict Smoke → Regression → Golden order;
- a challenger that fails an earlier gate is stopped before later, more expensive tiers;
- pre-Golden comparisons may decide whether a challenger continues, but only a successful Golden comparison can become `eligible_for_human_review`;
- Harness reports always set `auto_promoted=false` and `requires_human_approval=true`; the evaluator never mutates the live provider recipe, router, or QC policy;
- scorecard provenance includes recipe SHA-256, Dataset Registry manifest SHA-256, engine version, execution kind, quality mode, provider-recipe identity and QC-policy identity;
- comparisons reject incomplete runs, dataset-manifest drift and mismatched quality modes rather than comparing non-equivalent evidence;
- production-engine candidate evidence now records runtime semantic/technical QC scores, pass state, object-fidelity evidence, resolution score and analysis confidence;
- QC thresholds moved into a typed `QCPolicy` while built-in defaults preserve the previous behavior;
- `POD_QC_POLICY_PATH` and benchmark-recipe `qc_policy_path` are explicit opt-in mechanisms for testing a candidate policy; generated calibration artifacts are never loaded automatically;
- `QCPolicyCalibrator` uses approved-target Harness quality as labels, ignores ambiguous middle-quality results, searches bounded threshold candidates and enforces a configurable false-accept ceiling;
- safe calibration requires Golden Holdout runs by default and requires enough good/bad labeled evidence before recommending a threshold;
- metrics without enough evidence, such as sparse semantic-Judge object-fidelity coverage, keep the current threshold;
- each calibration produces `proposal.json` plus a full `candidate-qc-policy.json` under Harness storage for explicit re-benchmarking.

Example champion/challenger sequence:

```powershell
python -m pod_artwork_engine harness-suite historical-v1 `
  --champion config/benchmark-recipe.local.json `
  --challenger config/benchmark-recipe.remote.example.json `
  --quality-mode print_ready
```

Example review-only calibration after Golden runs:

```powershell
python -m pod_artwork_engine harness-calibrate <golden-run-id> <golden-run-id-2>
```

Phase 1F counterfactual router evidence and safe router calibration:

- routing thresholds are versioned in a typed `RouterPolicy`; the default policy preserves the earlier Phase 1 routing behavior;
- production can explicitly load a policy through `POD_ROUTER_POLICY_PATH`, while benchmark recipes may use a relative `router_policy_path`;
- Harness-only `harness_route_override` can force deterministic, hybrid or remote-semantic reconstruction without exposing a production API override;
- each production-engine benchmark case records selected route, requested override, design confidence, artwork type, required capabilities, provider availability, route reasons and whether remote reconstruction actually executed;
- `harness-route-matrix` runs the same cases through a deterministic baseline plus one or more remote-capable counterfactual routes;
- counterfactual comparisons are considered valid only when the remote reconstruction really executed; provider fallback-to-local is marked unusable rather than counted as remote evidence;
- route matrices measure per-case quality delta and latency while retaining the exact dataset fingerprint, quality mode and RouterPolicy identity;
- `harness-router-calibrate` proposes bounded confidence-threshold updates from measured deterministic-vs-remote winners;
- calibration prioritizes avoiding `false local` decisions, where deterministic routing would be selected for a case whose measured remote reconstruction was materially better;
- Golden Holdout matrices are required by default, thresholds need evidence on both sides of the decision boundary, and boolean routing features are not auto-learned;
- generated `candidate-router-policy.json` is review-only, never activated automatically, and must be re-benchmarked before production use.

Example route matrix:

```powershell
python -m pod_artwork_engine harness-route-matrix historical-v1 `
  --tier golden `
  --recipe config/benchmark-recipe.remote.example.json `
  --quality-mode print_ready `
  --route deterministic `
  --route hybrid
```

Example review-only router calibration:

```powershell
python -m pod_artwork_engine harness-router-calibrate <route-matrix-id>
```

Phase 1G guarded visual font identification:

- typography lines can carry typed `FontMatchEvidence`: matched family/style/weight, absolute score, winner margin, acceptance state, method, candidate count and exact font-file SHA-256;
- deterministic typography verifies the accepted font SHA-256 before rendering; if that file changed or disappeared, the renderer fails closed instead of substituting another same-family file;
- the matcher uses only installed Windows fonts plus `<data-root>/fonts`; it does not download a model or duplicate a font library into tool storage;
- OCR/provider line crops are converted into a color-independent foreground mask using alpha when available, otherwise border/background contrast;
- installed-font candidates are deduplicated and cheaply prefiltered by rendered text aspect before more expensive glyph-shape comparison;
- final ranking combines normalized glyph silhouette similarity with aspect fidelity;
- acceptance requires both a minimum absolute score and a minimum winner-vs-runner-up margin, so visually ambiguous font families are intentionally left unresolved;
- an unresolved result records evidence but does not write `font_family`; deterministic typography therefore keeps its existing fail-closed behavior;
- local visual evidence can override a provider font guess only when the same text line has an accepted local match;
- provider typography without local OCR can also be visually checked against the source when its line boxes are available;
- already accepted local matches are retained when provider evidence is merged, avoiding needless re-identification;
- optional font matching is failure-isolated: font scanning/rendering errors are logged and the job falls back to OCR/provider analysis instead of failing;
- Harness precision evidence includes `visual_font_match` coverage and matched-line counts;
- benchmark recipes can independently set `visual_font_match_enabled`, score/margin thresholds and candidate limits so Golden Holdout evidence can calibrate the feature.

Default configuration:

```text
POD_VISUAL_FONT_MATCH_ENABLED=1
POD_VISUAL_FONT_MATCH_MIN_SCORE=0.72
POD_VISUAL_FONT_MATCH_MIN_MARGIN=0.035
POD_VISUAL_FONT_MATCH_MAX_CANDIDATES=96
```

Phase 1H compound vector topology:

- `GeometryKind.PATH` now supports multiple typed subpaths in one primitive rather than forcing one `MOVE` sequence per primitive;
- each subpath may contain line and cubic Bezier commands; stroke-only paths may remain open while filled paths must close every subpath;
- typed `GeometryFillRule` makes `nonzero` versus `evenodd` explicit and the SVG exporter preserves the declared fill rule;
- deterministic raster rendering supports multi-subpath filled paths with explicit `evenodd`, allowing counters/holes such as rings, letter-like cutouts and nested logo shapes without painting a fake white inner object;
- compound `nonzero` fill remains valid for SVG export, but the local Pillow rasterizer fails closed until a true winding-number raster implementation is available; it never guesses hole direction;
- validation rejects empty subpaths, insufficient drawable segments, commands without an active subpath, open filled contours and commands that continue after `CLOSE` without a new `MOVE`;
- compound stroke-only geometry is supported, including independent line/Bezier marks in one path primitive;
- `GeometrySpec` carries evidence provider/version fields and successful deterministic geometry produces typed `GeometryTopologyEvidence`;
- topology evidence records primitive count, path count, total subpaths, compound-path count, even-odd compound-fill count and fill rules;
- the geometry topology checkpoint also records the SVG SHA-256, is copied into ArtifactManifest precision evidence, and is surfaced to Harness;
- Harness precision evidence now tracks `compound_geometry`, total geometry subpaths and even-odd compound fills in addition to ordinary vector coverage;
- engine status/diagnostics advertise only the geometry capabilities actually supported by the local deterministic renderer: cubic, multi-subpath and even-odd compound fill.

Phase 1I guarded illustration-aware text repair:

- `TypographyLine.replacement_mode=repair_local` is available for mixed artwork where text overlaps a non-solid background and a flat replacement color would visibly damage the design;
- the path requires an explicit guarded polygon mask and a separate `replacement_confidence`; default acceptance is `>=0.82`;
- local repair is deterministic and Pillow-only: each masked run is reconstructed from valid pixels on both sides horizontally and vertically, then the two directional estimates are combined;
- simple gradients and nearby color transitions can therefore be restored without adding a generative model, OpenCV, or another large local dependency;
- the existing text-region guard still bounds the polygon, preventing a repair mask from extending into unrelated illustration regions;
- if any masked pixel lacks sufficient surrounding boundary evidence, the repair raises `TypographyRenderUnavailable` and the engine leaves the region to the existing provider/fallback/review path;
- successful local repair records `local_text_repair` provenance in the job checkpoint and ArtifactManifest precision evidence, including method, threshold, repaired line confidence and mask size;
- Harness `PrecisionEvidence` reports `local_text_repair` coverage plus repaired-region counts, so the new path can be evaluated against historical targets instead of trusted by construction;
- benchmark recipes can independently enable/disable the feature and vary its confidence threshold;
- the feature is independently disableable through `POD_LOCAL_TEXT_REPAIR_ENABLED`, and its minimum evidence threshold is configurable through `POD_LOCAL_TEXT_REPAIR_MIN_CONFIDENCE`.

Default configuration:

```text
POD_LOCAL_TEXT_REPAIR_ENABLED=1
POD_LOCAL_TEXT_REPAIR_MIN_CONFIDENCE=0.82
```

Phase 1J standalone OCR runtime packaging:

- Tesseract discovery order is explicit and deterministic: configured `POD_TESSERACT_PATH`, bundled runtime beside the engine, system `PATH`, then standard Windows installation folders;
- bundled runtime layout is `runtime/tesseract/tesseract.exe` with `runtime/tesseract/tessdata/*.traineddata`;
- source/development runs look for the same runtime layout at the repository root, while a frozen PyInstaller engine resolves it relative to the engine executable;
- when a sibling `tessdata` directory exists, the OCR subprocess receives a scoped `TESSDATA_PREFIX`; the global parent-process environment is not rewritten;
- OCR results record backend source, backend version and executable SHA-256 without persisting the absolute executable path;
- the `local_ocr` checkpoint is copied into ArtifactManifest precision evidence, and Harness precision evidence records backend source/version for cohort comparison;
- `/status` and diagnostic bundles expose only redacted backend metadata (`configured`, `bundled`, `path`, `system_install`) rather than local filesystem paths;
- release assembly detects an approved `vendor/tesseract/` input and copies it to the canonical runtime layout;
- release validation treats the OCR runtime as optional, but if it is present it must contain `tesseract.exe` plus at least one `tessdata/*.traineddata` file;
- update packages include the runtime tree, and initial seed/update/rollback flows preserve that tree rather than retaining only the engine/desktop executables;
- `vendor/tesseract/` is Git-ignored so large third-party binaries and language packs cannot be accidentally committed;
- no large AI model is added by this phase, so the 40 GB storage architecture remains unchanged.

Standalone OCR release input:

```text
vendor/
  tesseract/
    tesseract.exe
    tessdata/
      eng.traineddata
      ...approved language packs...
```

Phase 2A guarded multi-reference evidence fusion:

- every multi-source job creates typed `ReferenceEvidence` and `MultiReferenceFusionEvidence` before analysis;
- reference quality combines artwork-region confidence, source quality and useful source resolution, producing one deterministic primary reference;
- local analyzer, OCR, deterministic reconstruction and candidate source provenance all use the same selected primary rather than independently re-ranking references at different stages;
- each artwork crop gets a lightweight visual signature composed of difference-hash structure, mean RGB evidence and crop aspect ratio;
- references are classified relative to the primary as `primary`, `consistent`, `ambiguous` or `conflicting`;
- fusion is evidence-level only: images are not averaged, warped together or pixel-composited, avoiding ghosting or invented detail when references show different views;
- high-quality conflicting evidence is converted into explicit `need_reference_disambiguation` plus `need_semantic_reconstruction` capabilities;
- when remote semantic processing is available, reference conflict receives an explicit Hybrid route reason `reference_conflict_remote_disambiguation`;
- provider requests still carry the full source set, so a capable semantic provider can inspect all references while deterministic local output remains anchored to the selected primary;
- if semantic resolution is unavailable, Print Ready / Max Fidelity remains fail-closed through the existing semantic QC requirement rather than treating local primary selection as proof of correctness;
- the `reference_fusion` checkpoint is included in ArtifactManifest precision evidence;
- Harness reports whether multi-reference fusion ran, total reference count, consistent/conflicting reference counts and consensus confidence;
- single-reference jobs produce a trivial typed fusion record and otherwise preserve the previous processing behavior;
- the stage is deterministic, CPU/Pillow-only and stores compact metadata, adding effectively no permanent model footprint.

The initial thresholds (`consistent >= 0.62`, high-quality conflict `< 0.42`, quality floor `>= 0.55`) are conservative engineering defaults. They are not domain-calibrated until historical Golden Holdout evidence demonstrates acceptable false-consistency and false-conflict rates.

Phase 2B coarse region confidence evidence map:

- a deterministic 4×4 evidence grid is expressed in normalized coordinates of the selected primary artwork crop;
- only Phase 2A references already classified as globally consistent are eligible to contribute supporting regional evidence;
- eligible references must additionally pass artwork-crop aspect compatibility (`>=0.90`), preventing strongly different views from being forced into the same coordinate grid;
- exact duplicate source hashes do not count as independent support;
- ambiguous, conflicting, duplicate and aspect-incompatible references are excluded from raising regional confidence;
- compatible crops are resized only into a small canonical comparison surface; this is evidence extraction, not output compositing;
- each cell compares local difference-hash structure plus mean RGB similarity and records `confidence`, `agreement`, `support_count`, `comparison_count` and typed normalized bbox coordinates;
- a supporting reference contributes positive local support only when local similarity is at least `0.58`; disagreement can therefore lower confidence in one region while leaving other cells supported;
- primary-only cells receive deliberately limited confidence, reflecting that one source alone is not multi-reference corroboration;
- aggregate evidence records mean/minimum confidence, support coverage and count of cells below the initial `0.60` low-confidence threshold;
- the map is checkpointed, copied into ArtifactManifest precision evidence and surfaced through Harness metrics;
- region-evidence computation is failure-isolated: missing/unsafe crop evidence produces a zero-confidence unavailable record instead of failing reconstruction;
- Phase 2B does not yet alter pixels, auto-repair regions or claim geometric alignment; it establishes a bounded evidence layer for later region rescue/QC policy.

Phase 2C fail-closed region rescue planning:

- every job derives a typed `RegionRescuePlanEvidence` from the Phase 2B region map;
- only low-confidence/disagreement/no-consensus cells are eligible rescue targets;
- horizontal low-confidence runs merge vertically only when the column span is identical, so every merged bbox is an exact rectangle fully backed by low-confidence cells;
- irregular target shapes remain split instead of expanding across a supported/good cell;
- dispositions are `none`, `semantic_provider_candidate` and `manual_review`;
- unavailable regional evidence and global multi-reference conflicts force `manual_review` with `fail_closed=true`;
- low-confidence target regions become semantic-provider candidates only when the provider is available; planner output alone never authorizes reconstruction;
- without a provider, the same bounded targets remain available to review but the planner fails closed;
- each target retains normalized bbox, exact grid cells, mean/maximum confidence, reason codes and required capabilities;
- the planner checkpoint is copied into ArtifactManifest precision evidence and Harness reports planner coverage, disposition, target counts and fail-closed status;
- Phase 2C does not call the provider, enter `REGION_RESCUE`, retry work, or mutate candidate/final pixels;
- the planner adds only compact JSON metadata and therefore does not materially change the 40 GB storage envelope.

Phase 2D conservative vector/raster representation planning:

- a typed `RepresentationPlanEvidence` is generated after DesignSpec analysis and before route/reconstruction;
- logical components are classified independently as typography, geometry, illustration/texture or unknown content;
- overall representation is derived as vector, raster or hybrid without changing the production renderer;
- typography is considered vector-ready only when text/layout confidence is sufficient, every font family is resolved, aggregate font-match confidence is at least `0.70` and every line has an accepted font-match record;
- exact-text mismatch, weak layout/line evidence or unresolved/unaccepted fonts cause raster fallback with explicit missing capabilities;
- geometry/logo is considered vector-ready only when explicit primitives exist, GeometrySpec confidence is at least `0.80` and every primitive has confidence at least `0.70`;
- absent/weak geometry falls back to raster with `need_vector_geometry` or `need_geometry_verification` provenance;
- illustration, texture, semantic-reconstruction content, perspective and occlusion remain raster-preservation candidates rather than being implicitly vectorized;
- mixed designs can produce hybrid plans, such as verified vector typography over raster illustration;
- unknown content remains raster and sets `fail_closed=true` with `need_content_classification`;
- the `representation_plan` checkpoint is copied into ArtifactManifest precision evidence and Harness reports plan coverage, overall representation, vector/raster component counts and fail-closed status;
- API/status diagnostics expose `representation_plan_v1` as a capability version only;
- Phase 2D does not alter route selection, provider requests, deterministic rendering or final pixels;
- no model, cache or persistent image derivative is added by the planner, so storage impact is negligible.

Phase 2E material-separation evidence planning:

- the selected primary reference receives typed `MaterialSeparationEvidence` after DesignSpec analysis;
- material disposition is one of `existing_alpha`, `simple_border_background`, `semantic_required` or `manual_review`;
- alpha-channel presence alone is not accepted as transparency evidence: the planner measures transparent and visible pixel fractions and distinguishes opaque RGBA from meaningful source alpha;
- existing meaningful alpha is retained as the highest-confidence separation evidence and does not require border-background inference;
- simple border-background extraction is only a candidate when source-edge RGB statistics are highly uniform (initial threshold `>= 0.90`), foreground/background contrast is measurable, artwork localization confidence is sufficient and the artwork bbox does not touch the source edge;
- border uniformity is measured from downsampled edge strips, while foreground contrast compares the detected artwork-region mean against the border mean;
- illustration/mixed artwork, fine-detail texture, material-like edge complexity, perspective, occlusion, heavy compression, nonuniform background or explicit semantic-reconstruction requirements fail closed to `semantic_required`;
- missing/weak artwork localization, very weak source quality, extremely low foreground/background contrast or missing bbox fail closed to `manual_review`;
- evidence records normalized bbox, alpha coverage, border uniformity, edge-contact ratio, foreground contrast, confidence, reason codes and missing capabilities;
- the `material_separation` checkpoint is copied into ArtifactManifest precision evidence and Harness reports disposition, confidence, fail-closed state and core material metrics;
- API/status diagnostics expose only `material_separation_evidence_v1` as the method version;
- Phase 2E does not alter `_extract_alpha_from_background`, route selection, provider calls, reconstruction pixels or final output;
- `simple_border_background` means “eligible for later benchmarked policy,” not “current border masking has been proven correct”;
- the evidence pass uses Pillow statistics only and adds no model/cache footprint.

The initial thresholds are conservative and must be calibrated against historical/Golden Holdout material-separation cases before they can control reconstruction behavior.

Phase 2F difficult-texture evidence planning:

- every analyzed primary artwork derives typed `TextureHandlingEvidence` after region and material evidence are available;
- disposition is `preserve_raster`, `local_detail_enhancement_candidate`, `semantic_required` or `manual_review`;
- the planner computes lightweight grayscale edge density, local contrast, neighboring-pixel variation and native artwork-crop long-edge resolution;
- those measurements are combined with preflight source quality/compression, region mean confidence, material-separation disposition and DesignSpec texture/perspective/occlusion evidence;
- smooth/painterly texture is preserved as raster by default;
- fine detail with sufficient native resolution, source quality, low compression and reliable regional evidence is also preserved without unbenchmarked sharpening;
- fine detail becomes `semantic_required` when source quality is below `0.70`, compression risk is at least `0.40`, native artwork long edge is below `1200`, source-compression evidence exists or region confidence is below `0.45`;
- outlined detail can be marked `local_detail_enhancement_candidate` only when source quality is at least `0.65`, compression risk is below `0.35`, native long edge is at least `900`, region confidence is at least `0.45`, and measured edge/contrast/variation evidence is present;
- perspective above `0.10`, occlusion above `0.10` or explicit semantic-reconstruction requirements force semantic handling;
- `semantic_required` material separation propagates to texture handling, while unresolved material separation or unavailable/very weak region evidence propagates to manual review;
- the checkpoint is copied into ArtifactManifest precision evidence and Harness reports coverage, disposition, confidence, fail-closed state, edge density, local contrast/variation and native long-edge resolution;
- API/status diagnostics expose `texture_handling_evidence_v1` only;
- Phase 2F does not invoke sharpening, super-resolution, provider rescue or any pixel mutation; local enhancement remains a benchmark candidate only;
- the planner is Pillow/CPU-only and adds no persistent model/cache footprint.

Texture thresholds are implementation defaults, not learned production policy. Golden Holdout detail/texture cohorts must prove local enhancement or SR behavior before execution is enabled.

Current truthfulness limits:

- Phase 2A is reference evidence fusion and stable primary selection, not geometric multi-view registration, dewarping or region-level compositing;
- ambiguous side/perspective/occluded views may remain unresolved, and Phase 2A does not claim to recover detail hidden in every reference;
- the initial reference-similarity thresholds require Golden Holdout calibration before being treated as domain-optimal;
- the source repository now supports a zero-dependency bundled OCR runtime, but it intentionally does not ship third-party Tesseract binaries until redistribution/licensing and the desired language-pack set are approved;
- visual font matching identifies the best candidate only from fonts actually present on the machine/user font directory; it cannot recover an unavailable proprietary font;
- `TypographyLine.bbox` used for local font verification is interpreted inside the detected artwork region; heavy perspective, curved text, occlusion or textured fills may reduce confidence and intentionally leave the font unresolved;
- exact typography redraw still requires sufficiently confident layout plus an accepted installed/local font match or other sufficiently trusted explicit typography evidence;
- font aliases canonicalize the same font family; they are not permission to swap to a visually similar different family;
- polygon text masks may use flat fill or guarded local boundary interpolation; highly textured/structural illustration repair still requires semantic rescue or review rather than pretending interpolation recovered hidden ground truth;
- compound `nonzero` fills are preserved in SVG but are not rasterized locally; deterministic raster output requires explicit `evenodd` for multi-subpath fills;
- the current geometry layer consumes validated vector evidence; fully automatic raster-to-vector tracing of arbitrary complex logos remains a separate benchmarked capability rather than an implicit conversion;
- route-matrix evidence is not valid as remote evidence when the provider was unavailable and the engine fell back locally;
- router calibration currently changes confidence thresholds only; `quick_2d_remote_first` and `remote_complex_enabled` remain explicit human-controlled policy fields;
- generated QC/router candidate policies are not production policy until explicitly configured and benchmarked again;
- border-color alpha extraction remains a baseline for clean/flat inputs, not a substitute for material separation on difficult garment photos;
- `REVIEW_REQUIRED` remains expected when semantic reconstruction is needed but no acceptable provider/local result exists;
- concrete GPT/provider model aliases remain configurable until Golden Holdout evidence selects them.

Remaining Phase 1 work:

- import and run the user's real historical source/final pairs through the suite and route matrix, then establish the first measured champion and RouterPolicy;
- supply the approved Tesseract runtime/language packs for release builds and validate OCR quality/routing on Golden Holdout;
- calibrate visual-font score/margin thresholds on real historical typography cases and build a curated user-font library where licensing permits;
- select concrete GPT/provider aliases and fallback mappings from measured quality, latency and cost rather than hand-coding them.

Build:

- pre-flight;
- artwork detection;
- DesignSpec;
- deterministic text/logo route;
- remote-first semantic reconstruction path;
- lightweight CPU/GPU local precision stages;
- JEV validation at provider and QC boundaries;
- alpha;
- final export;
- two-stage QC;
- storage-budget enforcement and resource telemetry.

### Phase 2 — Hybrid quality

Phase 2A–2F foundation is implemented:

- guarded multi-reference evidence fusion;
- deterministic primary-reference selection;
- conflict/ambiguity provenance and semantic-disambiguation routing;
- coarse normalized 4×4 region confidence/evidence maps;
- exclusion of ambiguous/conflicting/duplicate/aspect-incompatible references from regional support;
- fail-closed bounded region-rescue planning without pixel mutation;
- conservative component-level vector/raster/hybrid representation planning;
- material-separation evidence/readiness planning without alpha/output mutation;
- difficult-texture/detail readiness planning without sharpening/SR/output mutation;
- Harness coverage for global multi-reference consensus, regional confidence, rescue-plan decisions, representation plans, material-separation evidence and texture-handling evidence.

Remaining Phase 2 work:

- true geometric multi-reference alignment/dewarp and region-level registration;
- benchmarked execution policy for material separation;
- execution of the planned vector/raster split after benchmark calibration;
- benchmarked execution policy for local detail enhancement/difficult textures;
- SR comparison;
- Golden Holdout calibration before enabling targeted rescue execution.

### Phase 3 — Reliability

Build:

- smart capability router;
- provider fallback;
- region rescue;
- bounded retry;
- crash resume;
- resource-aware scheduling;
- manual review.

### Phase 4 — Learning

Build:

- Experience Store;
- similar-case retrieval;
- Recipe memory;
- structured user feedback;
- policy/prompt analytics;
- Champion/Challenger and shadow evaluation.

### Phase 5 — Domain optimization

Evaluate:

- local model adaptation;
- fine-tune/LoRA where justified;
- synthetic paired dataset;
- improved ranker/QC models;
- batch workflows.

### Phase 6 — Expansion

Optional:

- VPS synchronization;
- multiple local/GPU workers;
- cloud GPU workers;
- Creative Asset Manager integration;
- external API.

## 31. Non-goals and truthfulness

- Do not claim 4500×5400 or 300-DPI metadata alone proves print quality.
- Do not claim exact recovery of information hidden or destroyed in the source.
- Do not report synthesized/SR-invented detail as recovered ground truth.
- Do not vectorize complex raster texture merely to label the output “vector”.
- Do not lock the architecture to a single AI provider before benchmark evidence.
- Do not let learning automatically modify production without regression and promotion gates.
- Do not require VPS availability for the basic standalone local workflow.

## 32. Open benchmark decisions

The current hardware and deployment profile are now fixed inputs for V1:

```text
Xeon E5-2680 v4
28 cores / 56 logical processors
64 GB RAM
Radeon RX 470 8 GB
SSD
Standalone Windows tool
Remote-first semantic reconstruction
40 GB absolute tool-storage cap
```

Keep these configurable until Harness evidence is available:

- exact remote reasoning/image-provider mappings;
- exact OCR stack;
- which lightweight SR/matting stages benefit from CPU vs RX 470 acceleration;
- vectorization/tracing engine;
- optional experimental local reconstruction model, if any;
- candidate count by quality mode;
- retry/fallback limits;
- QC thresholds;
- provider budget limits;
- exact P50/P90 latency targets after real benchmark runs;
- when region rescue is preferable to full retry;
- whether any local model provides enough quality/latency benefit to justify permanent disk space under the 40 GB cap.

Any validated decision that changes production behavior should update this document so the repository retains one canonical technical history.
