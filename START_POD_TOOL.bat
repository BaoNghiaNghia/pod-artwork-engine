@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0build\release\PODArtworkTool.exe" (
    start "" "%~dp0build\release\PODArtworkTool.exe"
    exit /b 0
)

echo POD Artwork Tool release build was not found.
echo Run: powershell -NoProfile -File scripts\build_desktop.ps1
exit /b 2
