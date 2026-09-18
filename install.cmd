@echo off
setlocal
cd /d "%~dp0"
if not exist "scripts\setup_windows.ps1" (
  echo [UGA] scripts\setup_windows.ps1 not found.
  exit /b 1
)
echo [UGA] Installing VLGameAgent into .venv
echo [UGA] For OCR/vision dependencies, edit this file and add -Vision after setup_windows.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows.ps1"
if errorlevel 1 (
  echo [UGA] Installation failed. See docs\setup-troubleshooting.zh-CN.md
  exit /b %errorlevel%
)
echo [UGA] Done. Next: check.cmd then start.cmd --help
