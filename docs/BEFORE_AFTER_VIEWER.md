# Pixel-accurate Before / After Inspection — v0.1.17

## Inspection UX
The Auto Enhance result card contains a full-width interactive viewer rather than
two fixed-height miniature previews. Controls:

- **So sánh kéo**: one registered image with drag-adjustable wipe/divider and
  keyboard-accessible range slider. At Fit mode, drag anywhere to adjust the wipe.
- **Song song**: two synchronized views with the same zoom and pan coordinates.
- **Khác biệt**: a local *bounded preview* pixel-difference heatmap and an average
  absolute change (%) / share of visibly different pixels. This quantifies change,
  NOT image fidelity or improvement. The analysis runs **only on entering** the
  difference tab, never on every upload.
- **Zoom**: Fit, 100%, 200%, 400%, relative to **source-image pixels** so a model
  doubling output resolution cannot change the apparent content scale. Pan the
  image with the mouse/touch at zoomed levels; double-click toggles Fit/100%.
- **View**: checkerboard, white or dark background to inspect transparent/dark art.
  Expand to full-window overlay; Escape closes it. The viewer uses the source
  image coordinate system for consistent Before/After comparison.
- The comparison is kept client-side; no history, screenshots, or duplicated
  artwork is stored in the Engine.

## Routing accuracy
A dark logo on a transparent canvas is **not** itself low-contrast artwork.
Dark-rescue is now limited to mostly opaque dark input canvases; dark, nearly
flat transparent POD shapes route to `logo_vector` and VTracer may provide SVG.
Users still choose manual modes and must review small features/lettering in SVG.

## Quality checks
- Compare-mode, zoom, background, fullscreen/Escape, keyboard slider and mobile
  no-overflow covered by `npm --prefix desktop run qa:ui`.
- Classification for opaque dark input versus transparent black artwork covered
  by `tests/test_auto_enhance.py`.
- Whole-image preview should be visible at Fit; zoom modes are for detailed review.
- Raster fallback can yield minimal visible difference. The difference metric
  is not a reason to mark an image as print ready. All outputs remain drafts.
