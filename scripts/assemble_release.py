from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

from pod_artwork_engine import __version__
from pod_artwork_engine.updater import (
    DESKTOP_EXECUTABLE,
    ENGINE_EXECUTABLE,
    OCR_RUNTIME_RELATIVE,
    validate_release_dir,
)


LAUNCHER_EXECUTABLE = "PODArtworkTool.exe"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def assemble(repo_root: Path, package_url: str | None = None, channel: str = "stable") -> dict:
    repo_root = repo_root.resolve()
    sources = {
        LAUNCHER_EXECUTABLE: repo_root / "build" / "bootstrap" / LAUNCHER_EXECUTABLE,
        DESKTOP_EXECUTABLE: repo_root / "desktop" / "src-tauri" / "target" / "release" / DESKTOP_EXECUTABLE,
        ENGINE_EXECUTABLE: repo_root / "build" / "engine" / ENGINE_EXECUTABLE,
    }
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing release build artifacts: " + ", ".join(missing))

    release_dir = repo_root / "build" / "release"
    package_dir = repo_root / "build" / "packages"
    shutil.rmtree(release_dir, ignore_errors=True)
    release_dir.mkdir(parents=True, exist_ok=True)
    package_dir.mkdir(parents=True, exist_ok=True)

    for name, source in sources.items():
        shutil.copy2(source, release_dir / name)

    # Ship an immutable benchmark recipe with the standalone app. The preflight
    # does not use this as Golden evidence; it only validates benchmark config.
    recipe_source = repo_root / "config" / "benchmark-recipe.local.json"
    if not recipe_source.is_file():
        raise FileNotFoundError(f"Missing benchmark recipe for release: {recipe_source}")
    recipe_target = release_dir / "config" / recipe_source.name
    recipe_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(recipe_source, recipe_target)

    ocr_runtime_source = repo_root / "vendor" / "tesseract"
    ocr_runtime_included = ocr_runtime_source.is_dir()
    if ocr_runtime_included:
        shutil.copytree(
            ocr_runtime_source,
            release_dir / OCR_RUNTIME_RELATIVE,
        )

    valid, reason = validate_release_dir(release_dir)
    if not valid:
        raise RuntimeError(f"assembled release is invalid: {reason}")

    package_path = package_dir / f"PODArtworkTool-{__version__}.zip"
    package_path.unlink(missing_ok=True)
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(release_dir / DESKTOP_EXECUTABLE, DESKTOP_EXECUTABLE)
        archive.write(release_dir / ENGINE_EXECUTABLE, ENGINE_EXECUTABLE)
        archive.write(recipe_target, recipe_target.relative_to(release_dir).as_posix())
        runtime_dir = release_dir / "runtime"
        if runtime_dir.is_dir():
            for runtime_file in sorted(runtime_dir.rglob("*")):
                if runtime_file.is_file():
                    archive.write(
                        runtime_file,
                        runtime_file.relative_to(release_dir).as_posix(),
                    )

    digest = sha256(package_path)
    sha_path = package_dir / f"PODArtworkTool-{__version__}.sha256"
    sha_path.write_text(digest + "\n", encoding="ascii")

    manifest_path: Path | None = None
    if package_url:
        manifest = {
            "version": __version__,
            "channel": channel,
            "package_url": package_url,
            "sha256": digest,
            "minimum_updater_version": "0.1.0",
        }
        manifest_path = package_dir / "release-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return {
        "version": __version__,
        "release_dir": str(release_dir),
        "launcher": str(release_dir / LAUNCHER_EXECUTABLE),
        "package": str(package_path),
        "sha256": digest,
        "ocr_runtime_included": ocr_runtime_included,
        "manifest": str(manifest_path) if manifest_path else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--package-url")
    parser.add_argument("--channel", default="stable")
    args = parser.parse_args()
    print(json.dumps(assemble(args.repo_root, args.package_url, args.channel), indent=2))


if __name__ == "__main__":
    main()
