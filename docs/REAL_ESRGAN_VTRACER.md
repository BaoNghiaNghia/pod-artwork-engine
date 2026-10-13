# Real-ESRGAN + VTracer (v0.1.16)

This release connects **real local models/tools** to the Auto Enhance upload pipeline.
It does not install CUDA/PyTorch or use paid image AI. All results remain **drafts**
until reviewed and sent through the existing print quality checks.

## Installation and provenance

- **Real-ESRGAN**: xinntao/Real-ESRGAN official Windows NCNN/Vulkan release
  v0.2.5.0, file `realesrgan-ncnn-vulkan-20220424-windows.zip`.
  Pinned SHA-256 `abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d`.
  Only required binaries and official `realesrgan-x4plus` and
  `realesrgan-x4plus-anime` model pairs are extracted.
  No third-party installer or .bat scripts from the downloaded ZIP are executed.
- **VTracer** Python wheel: version `0.6.15`, installed via
  `pip install -e .` along with other Python dependencies.
- Run `python scripts/install_enhance_models.py` to install/reverify the
  local vendor runtime. `scripts/build_desktop.ps1` runs this automatically.
  Binary weights are deliberately *not checked into Git*; they are packaged
  with each Windows release in `runtime/realesrgan/`.
- A freshly installed release uses the packaged binary and model files next
  to its Engine. Development uses `vendor/realesrgan/`. To override with an
  audited local installation, set `POD_REALESRGAN_DIR` to its folder.
- Approximate size: 45 MB official download, ~53 MB unpacked, far below the
  40 GB complete-tool storage budget.

## Automatic routing and behavior

- Flat logos, typography and cartoon/sticker art: the existing bounded PNG
  raster preview **plus a real SVG** via VTracer.
  The SVG is separately downloadable and preserves vector paths; it is not
  necessarily editable text or a perfect recreation of the original font.
- Opaque photos, metallic and painterly artworks: attempt Real-ESRGAN NCNN
  Vulkan with `realesrgan-x4plus`. If inference succeeds, the PNG is
  returned with `X-Enhance-Engine: real-esrgan-ncnn-vulkan`.
- Semi-transparent photos/cutouts, sources where 2x exceeds output size
  budgets, unsupported Vulkan drivers, missing runtime or timeout:
  **fail open to existing Pillow raster processing** without claiming AI ran;
  `X-Enhance-Engine: pillow-fallback`, and an explanatory reason.
- API: `GET /enhance/capabilities`, `POST /enhance/analyze`,
  `POST /enhance/run?mode=auto` (PNG), and
  `POST /enhance/vector?mode=auto` (SVG).
- Input limit remains 50 MiB, 30 megapixels. The Real-ESRGAN inference path
  runs only when an output of exactly 2x fits <=20M pixels and longest edge
  <=4500. This avoids memory spikes on 4500x5400 print canvases.
- The GUI displays the capability state, actual processing engine and SVG
  download if applicable. Files and model scratch artifacts use temporary
  directories with automatic cleanup; no permanent preview files are written.
- The local Windows test machine has an RTX 4070 Laptop GPU and completed a
  **real** `realesrgan-x4plus` inference from 176x120 to 352x240 pixels.

## Quality and operational constraints

- Raster super-resolution invents plausible detail; it does not guarantee
  faithful text, transparent hair edges or print readiness.
- SVG tracing can simplify brush textures and may alter fine letter contours.
  Always compare source/PNG/SVG before printing.
- All program invocations use fixed binaries + fixed model names with
  `shell=False`, a 150-second timeout, bounded images, serial UI jobs, and a
  process-local single-GPU semaphore (additional concurrent requests get a
  clearly labeled raster fallback instead of competing for VRAM).
- An isolated packaged-engine release smoke test verifies real Vulkan inference,
  the VTracer SVG endpoint, capability detection, and cleanup without touching
  production job records: `python scripts/smoke_enhance_release.py`.
- GPU acceleration is not required to operate the tool: it falls back to
  Pillow, while VTracer can work locally without a Vulkan GPU.
- Model availability does not imply GPU driver compatibility; the actual
  inference result controls the reported engine.
- Backend regression: `pytest tests/test_enhance_engines.py tests/test_auto_enhance.py`.
  UI regression: `npm --prefix desktop run qa:ui`. Windows release:
  `scripts/build_desktop.ps1` then `scripts/assemble_release.py`.

Official repositories:
- https://github.com/xinntao/Real-ESRGAN
- https://github.com/visioncortex/vtracer
