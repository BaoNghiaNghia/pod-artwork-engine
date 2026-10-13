"""Install the audited official Real-ESRGAN NCNN Windows runtime.

Downloads a version-pinned official GitHub release asset, checks its SHA-256
before extraction, and extracts an allowlist only. No third-party installer or
postinstall scripts. Idempotent. Kept under vendor/ for packaging.
"""
from __future__ import annotations
import hashlib
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "realesrgan"
CACHED = ROOT / ".codelocal" / "realesrgan-ncnn-vulkan-20220424-windows.zip"
SOURCE_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
    "realesrgan-ncnn-vulkan-20220424-windows.zip"
)
PINNED_SHA256 = "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d"
NAMES = (
    "realesrgan-ncnn-vulkan.exe", "vcomp140.dll",
    "models/realesrgan-x4plus.bin", "models/realesrgan-x4plus.param",
    "models/realesrgan-x4plus-anime.bin", "models/realesrgan-x4plus-anime.param",
)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


def installed() -> bool:
    return all((VENDOR / name).is_file() for name in NAMES) and (
        (VENDOR / "source.sha256").read_text(encoding="ascii").strip() == PINNED_SHA256
        if (VENDOR / "source.sha256").is_file() else False
    )


def install() -> None:
    if installed():
        print("Real-ESRGAN pinned runtime already installed:", VENDOR)
        return
    with tempfile.TemporaryDirectory(prefix="pod-model-install-") as directory:
        cache = Path(directory) / "release.zip"
        if CACHED.is_file() and digest(CACHED) == PINNED_SHA256:
            shutil.copy2(CACHED, cache)
        else:
            with urllib.request.urlopen(SOURCE_URL, timeout=60) as response, cache.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=1024 * 1024)
        if cache.stat().st_size > 70 * 1024 * 1024 or digest(cache) != PINNED_SHA256:
            raise RuntimeError("Pinned Real-ESRGAN archive failed checksum/size verification")
        staging = Path(directory) / "runtime"
        staging.mkdir()
        with zipfile.ZipFile(cache) as archive:
            members = {member.filename: member for member in archive.infolist()}
            for name in NAMES:
                member = members.get(name)
                if member is None or member.file_size > 40 * 1024 * 1024:
                    raise RuntimeError("Real-ESRGAN archive has missing/unexpected runtime file")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as dest:
                    shutil.copyfileobj(source, dest, length=1024 * 1024)
        (staging / "source.sha256").write_text(PINNED_SHA256 + "\n", encoding="ascii")
        VENDOR.mkdir(parents=True, exist_ok=True)
        for name in (*NAMES, "source.sha256"):
            src = staging / name
            dest = VENDOR / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    if not installed():
        raise RuntimeError("Real-ESRGAN installed files failed validation")
    print("Real-ESRGAN NCNN runtime installed:", VENDOR)


if __name__ == "__main__":
    install()
