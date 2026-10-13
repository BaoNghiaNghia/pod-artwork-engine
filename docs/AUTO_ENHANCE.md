# Auto Enhance Artwork — v0.1.14

## User flow
On the **Artwork** tab, selecting/uploading images automatically starts a local, bounded
two-stage analysis and enhancement process. Each input is classified and then processed
according to the chosen mode. There is no external API cost or key requirement.

- `POST /enhance/analyze` accepts one PNG/JPEG/WebP file, returns category,
  recommended mode, heuristic confidence, reasons, observed image features and modes.
- `POST /enhance/run?mode=auto` accepts one image, independently reclassifies and
  runs the corresponding **local raster** enhancement; returns a PNG with mode/category
  headers and `X-Enhance-Draft: true`. The frontend does not rely on an untrusted client
  category to route production processing.
- Manual override with mode selection restarts processing automatically. Supports:
  Typography, flat logos, stickers, photos, painterly/brush, metal, dark contrast, and
  conservative mixed cleanup.
- At most six selected files are processed sequentially for Auto Enhance; additional
  images remain available to the existing Reconstruction workflow. Inputs and enhanced
  previews are transmitted directly and held in memory. No new backend file or preview
  is stored, and browser object URLs are revoked on selection changes/unmount.
- The existing **Generate artwork** button and its job/print QC pipeline remain unchanged.

## Technical safeguards
- Each file <=16 MB; <=16 million source pixels. Maximum raster output <=20 million
  pixels and longest side <=4500, at most 2x local upscale. Transparent backgrounds are
  preserved. JPEG/WebP allowed only if decoded by Pillow safely.
- Algorithms use Pillow resampling, alpha preservation, modest channel-specific
  unsharp/brightness transformations. They **do not** synthesize missing semantic detail,
  guarantee OCR accuracy, generate true vectors/SVG, or call an image AI provider.
- The classifier is deterministic *heuristics*, not a trained classifier or calibrated
  probability. Confidence is explanatory; uncertain artwork gets conservative cleanup.
- Outputs are raster drafts for user review. They are **not** passed through the Print QC
  gate and should never be presented as print-ready. 300 DPI metadata does not imply
  extra intrinsic detail. Use the existing full artwork generation + QC workflow to
  produce a verified print export.

## Quality gates
- `python -m pytest tests/test_auto_enhance.py -q`: deterministic detection, transparency,
  dark rescue, photo override, PNG output, invalid mode and upload rejection.
- `npm --prefix desktop run build`, `npm --prefix desktop run qa:ui` (Vite dev server
  running) cover automatic upload->analyze->enhance and UI override, including mobile.
- Full Python regression tests should remain green before release.

## Next stage
For higher accuracy on real artwork types, collect approved, non-sensitive fixtures
and score per-category confusion/review rates. Add optional trained vision/OCR routing and
AI redraw as **separate opt-in** steps only after model access and evaluation gates exist.
