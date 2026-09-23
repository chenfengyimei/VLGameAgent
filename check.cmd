@echo off
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [UGA] .venv not found. Run install.cmd first.
  exit /b 1
)
echo [UGA] Running offline smoke checks. This does not operate the game.
"%PY%" -m apps.agent
if errorlevel 1 exit /b %errorlevel%
"%PY%" -m apps.capture_probe --help >nul
if errorlevel 1 exit /b %errorlevel%
"%PY%" -c "from uga.perception.text import RapidOcrProvider; assert RapidOcrProvider().available"
if errorlevel 1 exit /b %errorlevel%
echo [UGA] Runtime and OCR checks passed. Next: start.cmd --help
