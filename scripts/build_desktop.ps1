$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

& "$PSScriptRoot\build_engine.ps1"

Push-Location "desktop"
try {
    npm install
    npx tauri build --no-bundle --ci false
}
finally {
    Pop-Location
}

& "$PSScriptRoot\build_bootstrap.ps1"
& ".venv\Scripts\python.exe" "scripts\assemble_release.py"

Write-Host "Desktop release build complete."
