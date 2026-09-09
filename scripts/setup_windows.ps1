[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"

& $Python -m venv $venvPath
if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
$venvPython = Join-Path $venvPath "Scripts\python.exe"
& $venvPython -m pip install --require-hashes -r (Join-Path $projectRoot "requirements-lock.txt")
if ($LASTEXITCODE -ne 0) { throw "Locked dependency installation failed" }
& $venvPython -m pip install --no-deps --no-build-isolation -e $projectRoot
if ($LASTEXITCODE -ne 0) { throw "Project installation failed" }
& $venvPython -m unittest discover -s (Join-Path $projectRoot "tests") -v
if ($LASTEXITCODE -ne 0) { throw "Unit tests failed" }
