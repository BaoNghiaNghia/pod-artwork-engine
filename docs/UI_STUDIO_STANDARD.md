# POD Artwork Studio — Compact UI Standard

The Windows app retains Tauri 2 + React/Vite + FastAPI/Python engine. UI changes never modify the job model, provider contracts, QC output gates or source/version retention.

## Design direction and framework
- **Recommended long-term UI system:** shadcn/ui (Radix primitives + Tailwind) with POD-specific design tokens. It is source-owned, suitable for complex artwork editors and can be adopted incrementally.
- **Implemented in v0.1.10:** shadcn/ui source-owned components (Button, Card, Input, Textarea, Badge, Progress, Tooltip, Separator), Tailwind CSS v4 Vite plugin, Radix primitives, CVA/clsx/tailwind-merge, Lucide icons, and a consistent POD theme in `src/tailwind.css`. The legacy styles remain temporarily to avoid regressions while shared controls are migrated. Future UI must use shadcn components rather than adding new legacy CSS-only controls.
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
- Automated browser smoke QA: start the dev server with `npm --prefix desktop run dev`, then run `npm --prefix desktop run qa:ui`. The check uses installed Chrome through Playwright (no bundled browser download), covers Artwork, AI Image Chat, Datasets, and AI Settings at 1280×800, 1024×768, 768×1024 and 390×844; it catches horizontal overflow, JavaScript exceptions, broken navigation, and disabled-control regressions. Set `POD_UI_QA_URL` or `POD_UI_QA_BROWSER` to override defaults. No screenshots, API keys or artwork are saved.
- Native Windows UI capture and interaction is a separate release gate when Windows capture tooling is available. Browser QA does not prove all WebView2/Tauri-specific behavior; do not claim native QA passed on the basis of browser tests.
- Audit icon+text consistency for every future new section. Prefer scoped tokens and compact responsive layout instead of scattered CSS overrides.
