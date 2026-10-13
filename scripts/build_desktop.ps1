$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not (Test-Path ".venv\\Scripts\\python.exe")) { python -m venv .venv }

# Pin/check the official NCNN archive, only extract allowlisted model binaries.
& ".venv\Scripts\python.exe" "scripts\install_enhance_models.py"
if ($LASTEXITCODE -ne 0) { throw "Real-ESRGAN model installation failed" }

& "$PSScriptRoot\build_engine.ps1"
if ($LASTEXITCODE -ne 0) {
    throw "Engine build failed with exit code $LASTEXITCODE"
}

Push-Location "desktop"
try {
    Remove-Item Env:CI -ErrorAction SilentlyContinue

    npm install
    if ($LASTEXITCODE -ne 0) {
        throw "npm install failed with exit code $LASTEXITCODE"
    }

    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop frontend build failed with exit code $LASTEXITCODE"
    }

    npm run tauri build -- --no-bundle
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri desktop build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

& "$PSScriptRoot\build_bootstrap.ps1"
if ($LASTEXITCODE -ne 0) {
    throw "Bootstrap build failed with exit code $LASTEXITCODE"
}

& ".venv\Scripts\python.exe" "scripts\assemble_release.py"
if ($LASTEXITCODE -ne 0) {
    throw "Release assembly failed with exit code $LASTEXITCODE"
}

Write-Host "Desktop release build complete."