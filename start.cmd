@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="--help" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_game_agent.ps1" -Help
  exit /b %errorlevel%
)
if "%~1"=="" (
  echo [UGA] Missing launch arguments. Run start.cmd --help.
  echo [UGA] Quick guide: START_HERE.zh-CN.md
  exit /b 1
)
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [UGA] .venv not found. Run install.cmd first.
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_game_agent.ps1" %*
exit /b %errorlevel%
