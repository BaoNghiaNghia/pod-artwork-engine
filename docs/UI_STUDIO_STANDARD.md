# POD Artwork Studio — Compact UI Standard

The Windows app retains Tauri 2 + React/Vite + FastAPI/Python engine. UI changes never modify the job model, provider contracts, QC output gates or source/version retention.

## Design direction and framework
- **Recommended long-term UI system:** shadcn/ui (Radix primitives + Tailwind) with POD-specific design tokens. It is source-owned, suitable for complex artwork editors and can be adopted incrementally.
- **Immediate zero-migration baseline:** existing React + CSS, with `studio.css` as the non-breaking visual layer and `lucide-react` for uniform icons. Do not mix multiple component libraries in the same control surface.
- Alternative: Mantine for faster CRUD forms, onboarding and overlays; Fluent UI 2 for Windows-native styling. Neither requires changing the Tauri shell.
- Avoid Electron migration: it adds runtime weight while the existing Tauri/WebView2 implementation works.

## Standard desktop layout
- Header: 65px brand, version, backend status; secondary runtime metadata.
- Navigation: 184px sidebar, contextual links, icons; horizontal compact tabs at <=790px.
- Workspaces: full remaining width; reference art and processing side-by-side; results compact when no job exists.
- AI chat: image viewport primary, version list + prompt secondary. Empty state shorter; export remains QC gated.
- Dataset: preflight is primary; import and golden holdout settings are progressive disclosure, without removing existing controls.
- Provider settings: endpoint and API key are primary, model alias/timeouts tucked under Advanced.
- Controls: 36–38px compact height, 12px card radius, 8/12/16px spacing cadence, accessible focus indicators.

## Implementation and QA gates
- Never expose print-ready download from chat draft version.
- All data-layer operations, source preservation, provider sessions and retry semantics unchanged.
- Test Tauri build with `npm --prefix desktop run build`.
- Browser QA at 1180×780, 900×650, 768×1024 and 390×844. Check keyboard access, no horizontal overflow, image containment, filename truncation, sidebar collapse.
- Audit icon+text consistency for every future new section. Prefer scoped tokens and compact responsive layout instead of scattered CSS overrides.
