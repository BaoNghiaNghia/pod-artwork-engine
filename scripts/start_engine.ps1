param(
    [string]$DataRoot = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if ($DataRoot) {
    $env:POD_ARTWORK_DATA = $DataRoot
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating Python virtual environment..."
    python -m venv .venv
    & ".venv\Scripts\python.exe" -m pip install --upgrade pip
    & ".venv\Scripts\python.exe" -m pip install -e ".[dev]"
}

Write-Host "Starting POD Artwork Engine..."
& ".venv\Scripts\python.exe" -m pod_artwork_engine serve
