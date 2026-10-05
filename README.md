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

**Stage 1 — Standalone Foundation**

The repository currently establishes:

- separate desktop and engine processes;
- versioned typed contracts;
- persistent job state;
- structured non-blocking logging;
- storage quotas and cleanup;
- checkpoints/artifact folders;
- diagnostics;
- release-manifest/update primitives;
- local engine API;
- Windows startup scripts.

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

Desktop development will use the `desktop/` Tauri shell and the local engine process.
