# Auto Enhance — High Quality multi-candidate pipeline (v0.1.19)

## Profiles
- **Fast**: backwards-compatible, one `standard` raster candidate, existing
  conservative routing/fallback.
- **Balanced**: `standard` and `detail` candidates; model inference when
  appropriate, serial and bounded.
- **Max Quality** (desktop default): `standard`, `detail`, `texture`
  candidates. Prefer the `detail` preview when produced, but never approve an
  output automatically. Vector-friendly artwork additionally gets a separate
  **SVG Vector** candidate, using VTracer's high-resolution tracing preset.

The user selects candidates independently from the shared Before/After viewer
and downloads the selected PNG (or explicitly the SVG). Changing the selection
does *not* rerun the model. No candidate overwrites the original file.

## Quality routing
- Sticker/cartoon illustration: Real-ESRGAN `realesrgan-x4plus-anime` for
  the bounded **detail** candidate. Max Quality supports small transparent
  inputs: compose RGB onto a matte for inference, restore the original resampled
  alpha, blend against source color near the cutout boundary to avoid fringe.
- Opaque photo, painted, metallic detail: prefer `realesrgan-x4plus`.
- Artwork with typography / line art / flat logos: use VTracer SVG in addition
  to the raster candidates. VTracer uses up to 3200px input trace edge in
  Max Quality (1600px Fast, 2300px Balanced) and more precise curve settings.
  This does not recover the original font, original design intent or hidden text.
- Large print canvases (e.g. 4500 x 5400) retain their original
  dimensions and are never directly allocated as unsafe ~100 MP Vulkan 2x
  outputs. In Max Quality's **detail** candidate for AI-compatible artwork,
  an oversized canvas is split into overlapping 1536px tiles with 48px halos.
  Each visible tile receives genuine Real-ESRGAN 2x inference, is carefully
  resampled back to its source size, and merged without changing the original
  alpha mask. This is **AI detail refinement**, not a claim of new print
  resolution or recovered missing text. At most 16 tiles are attempted with a
  420-second cooperative budget. Any failed/timeout tile discards the entire
  tiled candidate and explicitly falls back to Pillow HQ; no partly processed
  image is served. Purely transparent tiles require no GPU work.
- An active Vulkan model uses one GPU process at a time. **Max Quality waits
  up to 90 seconds for the GPU slot** rather than falling back immediately;
  Fast retains immediate fallback. If inference is unavailable, too large or
  fails, return a clearly labeled **Pillow HQ** candidate with the reason.
  No fake AI success.
- Max Quality automatically handles up to two source files per batch and
  Balanced up to three, to bound working memory. Remaining input files stay
  in References. Work is done serially; no background processing or persistence.

## API
`POST /enhance/run?mode=auto&profile=max_quality&variant=detail`

Profile enum: `fast`, `balanced`, `max_quality`.
Variant enum: `standard`, `detail`, `texture`. Unsupported combinations
return 422. Existing calls default to Fast+standard.

Response: PNG with `X-Enhance-Engine`, `X-Enhance-Profile`,
`X-Enhance-Variant`, `X-Enhance-Technical-Score`,
`X-Enhance-Edge-Gain`, `X-Enhance-Alpha-Drift`,
`X-Enhance-Draft: true`.

`POST /enhance/vector?mode=vector_clean&profile=max_quality` returns SVG
with `X-Enhance-Vector-Preset` and the existing safety restrictions.

## Quality scoring is diagnostic, not a print QC pass
The per-candidate technical score (0-100) is a **small-image heuristic** of
edge energy, grayscale drift and alpha drift. It is NOT a learned image quality
assessor, OCR, semantic fidelity check, human rating, or proof that one output
looks better. Excessive sharpening or inaccurate text can still receive a
misleading score. **All outputs remain `print_ready: false` and require
manual visual review plus the existing print QC pipeline.**

## Tests and provenance
- Backend: `pytest tests/test_quality_enhance.py tests/test_enhance_engines.py`
  covers profile validation, candidate results, alpha integrity, 422, HDR
  texture variations and model-name routing.
- Frontend: `npm --prefix desktop run qa:ui` covers three candidates,
  selection without network reruns, comparison and responsive layout.
- Real hardware test was run against the local RTX 4070 Laptop GPU using the
  pinned Real-ESRGAN anime model. 176x120 transparent test artwork became
  352x240 with alpha intact; VTracer also returned valid SVG paths.
- No new model downloads are required beyond the pinned Real-ESRGAN and
  VTracer components from v0.1.16. Existing 50 MB/file and 30 MP input caps
  remain. No external AI API charge.
