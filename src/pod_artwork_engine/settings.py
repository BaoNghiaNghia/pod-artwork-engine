from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import psutil


GIB = 1024 ** 3
MIB = 1024 ** 2


@dataclass(frozen=True)
class StorageLimits:
    soft_total_bytes: int = 32 * GIB
    hard_total_bytes: int = 40 * GIB
    cache_bytes: int = 5 * GIB
    temp_jobs_bytes: int = 6 * GIB
    logs_bytes: int = 1 * GIB
    updates_bytes: int = 2 * GIB
    harness_bytes: int = 2 * GIB


@dataclass(frozen=True)
class Settings:
    data_root: Path
    host: str = "127.0.0.1"
    port: int = 8765
    cpu_soft_threads: int = 40
    ram_soft_bytes: int = 32 * GIB
    ram_hard_bytes: int = 48 * GIB
    max_concurrent_jobs: int = 0
    storage: StorageLimits = StorageLimits()
    release_channel: str = "stable"
    release_manifest_url: str = ""
    remote_provider_url: str = ""
    remote_provider_token: str = ""
    remote_provider_name: str = "remote"
    remote_provider_timeout_seconds: float = 120.0
    remote_provider_failure_threshold: int = 3
    remote_provider_cooldown_seconds: float = 30.0
    provider_recipe_path: Path | None = None
    qc_policy_path: Path | None = None
    router_policy_path: Path | None = None
    local_ocr_enabled: bool = True
    visual_font_match_enabled: bool = True
    visual_font_match_min_score: float = 0.72
    visual_font_match_min_margin: float = 0.035
    visual_font_match_max_candidates: int = 96
    local_text_repair_enabled: bool = True
    local_text_repair_min_confidence: float = 0.82
    tesseract_path: Path | None = None
    tesseract_language: str = "eng"

    @classmethod
    def from_env(cls) -> "Settings":
        default_root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PODArtworkTool"
        root = Path(os.environ.get("POD_ARTWORK_DATA", default_root))

        logical = psutil.cpu_count(logical=True) or 8
        total_ram = psutil.virtual_memory().total
        default_threads = min(40, max(4, logical - max(4, logical // 4)))
        default_ram_soft = max(4 * GIB, int(total_ram * 0.50))
        default_ram_hard = max(default_ram_soft, int(total_ram * 0.75))

        return cls(
            data_root=root,
            host=os.environ.get("POD_ENGINE_HOST", "127.0.0.1"),
            port=int(os.environ.get("POD_ENGINE_PORT", "8765")),
            cpu_soft_threads=int(os.environ.get("POD_CPU_SOFT_THREADS", default_threads)),
            ram_soft_bytes=int(float(os.environ.get("POD_RAM_SOFT_GB", default_ram_soft / GIB)) * GIB),
            ram_hard_bytes=int(float(os.environ.get("POD_RAM_HARD_GB", default_ram_hard / GIB)) * GIB),
            max_concurrent_jobs=max(0, int(os.environ.get("POD_MAX_CONCURRENT_JOBS", "0"))),
            release_channel=os.environ.get("POD_RELEASE_CHANNEL", "stable"),
            release_manifest_url=os.environ.get("POD_RELEASE_MANIFEST_URL", ""),
            remote_provider_url=os.environ.get("POD_REMOTE_PROVIDER_URL", ""),
            remote_provider_token=os.environ.get("POD_REMOTE_PROVIDER_TOKEN", ""),
            remote_provider_name=os.environ.get("POD_REMOTE_PROVIDER_NAME", "remote"),
            remote_provider_timeout_seconds=float(
                os.environ.get("POD_REMOTE_PROVIDER_TIMEOUT_SECONDS", "120")
            ),
            remote_provider_failure_threshold=max(
                1, int(os.environ.get("POD_REMOTE_PROVIDER_FAILURE_THRESHOLD", "3"))
            ),
            remote_provider_cooldown_seconds=max(
                1.0, float(os.environ.get("POD_REMOTE_PROVIDER_COOLDOWN_SECONDS", "30"))
            ),
            provider_recipe_path=(
                Path(os.environ["POD_PROVIDER_RECIPE_PATH"]).expanduser()
                if os.environ.get("POD_PROVIDER_RECIPE_PATH")
                else None
            ),
            qc_policy_path=(
                Path(os.environ["POD_QC_POLICY_PATH"]).expanduser()
                if os.environ.get("POD_QC_POLICY_PATH")
                else None
            ),
            router_policy_path=(
                Path(os.environ["POD_ROUTER_POLICY_PATH"]).expanduser()
                if os.environ.get("POD_ROUTER_POLICY_PATH")
                else None
            ),
            local_ocr_enabled=os.environ.get("POD_LOCAL_OCR_ENABLED", "1").strip().lower()
            not in {"0", "false", "no", "off"},
            visual_font_match_enabled=os.environ.get(
                "POD_VISUAL_FONT_MATCH_ENABLED",
                "1",
            ).strip().lower()
            not in {"0", "false", "no", "off"},
            visual_font_match_min_score=max(
                0.0,
                min(
                    1.0,
                    float(os.environ.get("POD_VISUAL_FONT_MATCH_MIN_SCORE", "0.72")),
                ),
            ),
            visual_font_match_min_margin=max(
                0.0,
                min(
                    1.0,
                    float(os.environ.get("POD_VISUAL_FONT_MATCH_MIN_MARGIN", "0.035")),
                ),
            ),
            visual_font_match_max_candidates=min(
                512,
                max(
                    8,
                    int(
                        os.environ.get(
                            "POD_VISUAL_FONT_MATCH_MAX_CANDIDATES",
                            "96",
                        )
                    ),
                ),
            ),
            local_text_repair_enabled=os.environ.get(
                "POD_LOCAL_TEXT_REPAIR_ENABLED",
                "1",
            ).strip().lower()
            not in {"0", "false", "no", "off"},
            local_text_repair_min_confidence=max(
                0.0,
                min(
                    1.0,
                    float(
                        os.environ.get(
                            "POD_LOCAL_TEXT_REPAIR_MIN_CONFIDENCE",
                            "0.82",
                        )
                    ),
                ),
            ),
            tesseract_path=(
                Path(os.environ["POD_TESSERACT_PATH"]).expanduser()
                if os.environ.get("POD_TESSERACT_PATH")
                else None
            ),
            tesseract_language=os.environ.get("POD_TESSERACT_LANGUAGE", "eng"),
        )

    def ensure_directories(self) -> None:
        for path in (
            self.data_root,
            self.jobs_dir,
            self.cache_dir,
            self.logs_dir,
            self.updates_dir,
            self.artifacts_dir,
            self.datasets_dir,
            self.harness_dir,
            self.fonts_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def jobs_dir(self) -> Path:
        return self.data_root / "jobs"

    @property
    def cache_dir(self) -> Path:
        return self.data_root / "cache"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    @property
    def updates_dir(self) -> Path:
        return self.data_root / "updates"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_root / "artifacts"

    @property
    def datasets_dir(self) -> Path:
        return self.data_root / "datasets"

    @property
    def harness_dir(self) -> Path:
        return self.data_root / "harness"

    @property
    def fonts_dir(self) -> Path:
        return self.data_root / "fonts"

    @property
    def database_path(self) -> Path:
        return self.data_root / "engine.sqlite3"
