$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

& "$PSScriptRoot\build_engine.ps1"

Push-Location "desktop"
try {
    Remove-Item Env:CI -ErrorAction SilentlyContinue
    npm install
    npm run build
    npm run tauri build -- --no-bundle
}
finally {
    Pop-Location
}

Write-Host "Desktop executable build complete."
