@echo off
setlocal
cd /d "%~dp0"
if not exist "scripts\setup_windows.ps1" (
  echo [UGA] scripts\setup_windows.ps1 not found.
  exit /b 1
)
echo [UGA] Installing VLGameAgent into .venv
echo [UGA] Installing the locked runtime plus OCR/vision dependencies.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows.ps1" -Vision
if errorlevel 1 (
  echo [UGA] Installation failed. See docs\setup-troubleshooting.zh-CN.md
  exit /b %errorlevel%
)
echo [UGA] Done. Next: check.cmd, then start.cmd --help
