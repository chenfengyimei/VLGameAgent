@echo off
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [UGA] .venv not found. Run install.cmd first.
  exit /b 1
)
if "%~1"=="--help" (
  start "" "%~dp0docs\setup-visual.zh-CN.html"
  "%PY%" -m apps.agent --help
  exit /b 0
)
echo [UGA] This shortcut opens help first. Use start.cmd --help for commands.
echo [UGA] Real game control still requires explicit runtime configuration and confirmation.
start "" "%~dp0docs\setup-visual.zh-CN.html"
