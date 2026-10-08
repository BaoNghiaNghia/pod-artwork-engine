from __future__ import annotations

import io
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps

from .historical_onboarding import HistoricalOnboardingReport


@dataclass(frozen=True)
class PreviewAsset:
    path: Path
    size: int
    modified_ns: int


@dataclass(frozen=True)
class PreviewPair:
    sources: tuple[PreviewAsset, ...]
    target: PreviewAsset


class PairPreviewSessions:
    """Ephemeral, read-only thumbnail access to preflight-validated pairs.

    URLs use unguessable tokens and numeric indices: callers cannot pass file
    paths to the image endpoint. Sessions are in-memory, bounded and expire.
    """

    def __init__(self, *, ttl_seconds: int = 900, max_sessions: int = 16) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[float, tuple[PreviewPair, ...]]] = {}

    @staticmethod
    def _asset(value: str) -> PreviewAsset:
        path = Path(value).resolve(strict=True)
        stat = path.stat()
        if not path.is_file():
            raise FileNotFoundError(path)
        return PreviewAsset(path, stat.st_size, stat.st_mtime_ns)

    def register(self, report: HistoricalOnboardingReport) -> str | None:
        if not report.pairs:
            return None
        try:
            pairs = tuple(
                PreviewPair(
                    sources=tuple(self._asset(path) for path in pair.source_paths),
                    target=self._asset(pair.target_path),
                )
                for pair in report.pairs
            )
        except (OSError, ValueError):
            # Do not alter preflight readiness/import semantics on preview failure.
            return None
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._sessions = {
                key: item
                for key, item in self._sessions.items()
                if item[0] > now
            }
            if len(self._sessions) >= self.max_sessions:
                oldest = min(self._sessions, key=lambda key: self._sessions[key][0])
                self._sessions.pop(oldest, None)
            self._sessions[token] = (now + self.ttl_seconds, pairs)
        return token

    def image(
        self,
        token: str,
        pair_index: int,
        role: Literal["source", "target"],
        source_index: int = 0,
        size: Literal["thumb", "large"] = "thumb",
    ) -> bytes:
        if pair_index < 0 or source_index < 0:
            raise LookupError("Invalid preview index")
        with self._lock:
            session = self._sessions.get(token)
            if session is None or session[0] <= time.monotonic():
                self._sessions.pop(token, None)
                raise LookupError("Preview expired; rerun preflight")
            pairs = session[1]
            if pair_index >= len(pairs):
                raise LookupError("Unknown preview pair")
            pair = pairs[pair_index]
            if role == "target":
                asset = pair.target
            elif role == "source" and source_index < len(pair.sources):
                asset = pair.sources[source_index]
            else:
                raise LookupError("Unknown preview source")

        try:
            current = asset.path.stat()
            if (
                current.st_size != asset.size
                or current.st_mtime_ns != asset.modified_ns
                or not asset.path.is_file()
            ):
                raise LookupError("Image changed since preflight; rerun preflight")
            with Image.open(asset.path) as original:
                if original.width * original.height > 150_000_000:
                    raise LookupError("Image exceeds preview safety limit")
                image = ImageOps.exif_transpose(original)
                max_edge = 1200 if size == "large" else 384
                image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                if image.mode != "RGBA":
                    image = image.convert("RGBA")
                # Keep alpha artwork visible on a subtle neutral background.
                canvas = Image.new("RGBA", image.size, (245, 246, 249, 255))
                canvas.alpha_composite(image)
                output = io.BytesIO()
                canvas.convert("RGB").save(output, format="WEBP", quality=78, method=3)
                return output.getvalue()
        except (OSError, ValueError) as exc:
            raise LookupError("Image thumbnail unavailable") from exc
