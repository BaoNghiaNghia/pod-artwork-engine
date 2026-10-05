from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from .settings import Settings


def normalize_font_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


DEFAULT_ALIASES: dict[str, str] = {
    "arialmt": "arial",
    "arialboldmt": "arial",
    "timesnewromanpsmt": "timesnewroman",
    "timesnewromanpsboldmt": "timesnewroman",
    "helveticaneue": "helvetica",
    "helveticaneuebold": "helvetica",
    "couriernewpsmt": "couriernew",
    "couriernewpsboldmt": "couriernew",
}


@dataclass(frozen=True)
class FontEntry:
    path: Path
    family: str
    style: str
    normalized_family: str
    normalized_stem: str
    inferred_weight: int


def _infer_weight(style: str, stem: str) -> int:
    value = f"{style} {stem}".casefold()
    if any(token in value for token in ("black", "heavy", "extrabold", "ultrabold")):
        return 900
    if any(token in value for token in ("bold", "semibold", "demibold", "demi")):
        return 700
    if any(token in value for token in ("medium",)):
        return 500
    if any(token in value for token in ("light", "thin", "extralight", "ultralight")):
        return 300
    return 400


class FontCatalog:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.aliases = dict(DEFAULT_ALIASES)
        self.aliases.update(self._load_user_aliases())
        self.entries = self._scan()

    def _roots(self) -> list[Path]:
        roots = [self.settings.fonts_dir]
        if os.name == "nt":
            windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
            roots.append(windows / "Fonts")
        return roots

    def _load_user_aliases(self) -> dict[str, str]:
        path = self.settings.data_root / "font_aliases.json"
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        aliases: dict[str, str] = {}
        for raw, canonical in payload.items():
            if not isinstance(raw, str) or not isinstance(canonical, str):
                continue
            aliases[normalize_font_name(raw)] = normalize_font_name(canonical)
        return aliases

    def _scan(self) -> list[FontEntry]:
        entries: list[FontEntry] = []
        seen: set[Path] = set()
        for root in self._roots():
            if not root.exists():
                continue
            for pattern in ("*.ttf", "*.otf", "*.ttc"):
                for path in root.glob(pattern):
                    resolved = path.resolve()
                    if resolved in seen:
                        continue
                    seen.add(resolved)
                    try:
                        font = ImageFont.truetype(str(resolved), size=16)
                        family, style = font.getname()
                    except Exception:
                        family, style = path.stem, ""
                    entries.append(
                        FontEntry(
                            path=resolved,
                            family=family,
                            style=style,
                            normalized_family=normalize_font_name(family),
                            normalized_stem=normalize_font_name(path.stem),
                            inferred_weight=_infer_weight(style, path.stem),
                        )
                    )
        return sorted(entries, key=lambda item: str(item.path))

    def canonicalize(self, family: str) -> str:
        normalized = normalize_font_name(family)
        return self.aliases.get(normalized, normalized)

    def resolve(self, family: str, weight: int = 400) -> FontEntry | None:
        wanted = self.canonicalize(family)
        if not wanted:
            return None

        candidates: list[tuple[int, int, str, FontEntry]] = []
        for entry in self.entries:
            family_key = self.aliases.get(entry.normalized_family, entry.normalized_family)
            stem_key = self.aliases.get(entry.normalized_stem, entry.normalized_stem)
            if wanted not in {family_key, stem_key}:
                continue
            if weight >= 650 and entry.inferred_weight < 600:
                continue
            if weight <= 350 and entry.inferred_weight > 400:
                continue
            weight_delta = abs(entry.inferred_weight - weight)
            family_penalty = 0 if family_key == wanted else 1
            candidates.append((family_penalty, weight_delta, str(entry.path), entry))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[:3])
        return candidates[0][3]

    def normalize_known_family(self, family: str) -> str:
        entry = self.resolve(family)
        return entry.family if entry is not None else family

@lru_cache(maxsize=8)
def get_font_catalog(settings: Settings) -> FontCatalog:
    return FontCatalog(settings)
