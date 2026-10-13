# AI Settings — single configuration page (v0.1.13)

All user-facing AI provider/model configuration is in **AI Settings**. The **Artwork** and **AI Image Chat** tabs do not contain separate credential or model forms.

## Connection 01 · Artwork Reconstruction
- Endpoint: `POST /provider/config`; read: `GET /provider/config`; optional test: `POST /provider/test`.
- POD-compatible HTTPS provider gateway implementing **analyze / reconstruct / judge**; **not** a direct OpenAI Images URL.
- Provider URL, name, model alias, timeout, and optional API token are managed in one card, with advanced fields collapsed initially.
- Existing provider configuration is read and updated through the same backend as earlier releases; no migration or key reset.

## Connection 02 · Image AI
- Endpoint: `POST /image-chat/config`; read: `GET /image-chat/config`.
- Direct OpenAI Images API: model (default `gpt-image-1`) and OpenAI API key.
- Keys remain in the running Engine session (or can be provided at startup through `POD_IMAGE_API_KEY` / `POD_IMAGE_MODEL`); the UI never writes keys to localStorage or application settings.
- Leaving the key blank preserves the currently configured key; an explicit **Clear Image AI session key** control clears it. After saving, the password input is emptied.
- AI Image Chat shows connection state and a shortcut to **AI Settings**, without an inline config form. Changing tabs re-fetches server settings, so returning to Chat picks up the new key/model.

## System diagnostics
Existing version/update/logger controls remain below the connection cards in the same AI Settings page and preserve their existing APIs.

## Release checks
- `npm --prefix desktop run build`
- `npm --prefix desktop run qa:ui` with a running Vite dev server
- E2E assertions: one Image AI credential form in Settings only; Chat shortcut navigation; mock POST model/key and clear password input; both providers visible; 16 viewport/screen checks.
- `python -m pytest -q` for unchanged Engine API backward compatibility.

There is **no unified secret** shared between the POD reconstruction gateway and OpenAI Images API: these are separate protocols that are configured on a single screen.
