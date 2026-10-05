$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

$Python = ".venv\Scripts\python.exe"
& $Python -m pip install -e ".[dev]"

$Dist = Join-Path $RepoRoot "build\bootstrap"
$Work = Join-Path $RepoRoot "build\pyinstaller-bootstrap"
Remove-Item $Dist -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue

& $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name "PODArtworkTool" --distpath $Dist --workpath $Work --specpath $Work "packaging\bootstrap_entry.py"

Write-Host "Bootstrap built: $Dist\PODArtworkTool.exe"
