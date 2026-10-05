$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

$Python = ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -e ".[dev]"

$Dist = Join-Path $RepoRoot "build\engine"
$Work = Join-Path $RepoRoot "build\pyinstaller"
Remove-Item $Dist -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue

& $Python -m PyInstaller --noconfirm --clean --onefile --name "pod-artwork-engine" --distpath $Dist --workpath $Work --specpath $Work "packaging\engine_entry.py"

$Source = Join-Path $Dist "pod-artwork-engine.exe"
$TargetDir = Join-Path $RepoRoot "desktop\src-tauri\binaries"
$Target = Join-Path $TargetDir "pod-artwork-engine-x86_64-pc-windows-msvc.exe"
New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
Copy-Item $Source $Target -Force

Write-Host "Engine sidecar built: $Target"
