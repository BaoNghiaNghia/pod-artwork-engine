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

$Python = ".venv\Scripts\python.exe"

# Settings already provides a stable manifest URL by default.
# Explicitly empty POD_RELEASE_MANIFEST_URL disables checking.
Write-Host "Checking POD Artwork Tool updates..."
try {
    $checkJson = & $Python -m pod_artwork_engine update-check
    if ($LASTEXITCODE -ne 0) { throw "Update check process failed" }
    $check = $checkJson | ConvertFrom-Json
    if ($check.update_available) {
        Write-Host "Update $($check.latest_version) found. Downloading and verifying..."
        & $Python -m pod_artwork_engine update-stage
        if ($LASTEXITCODE -ne 0) { throw "Update download or verification failed" }
        Write-Host "Update staged. Reopen the packaged launcher when ready to activate it."
    } elseif (-not $check.enabled) {
        Write-Host "Update channel is disabled."
    } else {
        Write-Host "POD Artwork Tool is up to date."
    }
}
catch {
    Write-Warning "Update check failed; keeping the installed version. $($_.Exception.Message)"
}

Write-Host "Applying storage cleanup policy..."
try {
    & $Python -m pod_artwork_engine cleanup | Out-Null
}
catch {
    Write-Warning "Storage cleanup failed: $($_.Exception.Message)"
}

Write-Host "Starting POD Artwork Engine..."
& $Python -m pod_artwork_engine serve