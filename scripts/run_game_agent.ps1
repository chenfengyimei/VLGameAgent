[CmdletBinding()]
param(
    [switch]$Help,
    [string]$Profile = "",
    [string]$Goal = "",
    [string[]]$GoalEvidence = @(),
    [string]$Model = "",
    [string]$BaseUrl = "",
    [string]$ApiKeyEnv = "UGA_VLM_API_KEY",
    [string]$Python = "",
    [int]$DashboardPort = 8787,
    [ValidateRange(0, 2147483647)][int]$DurationSeconds = 300,
    [switch]$Continuous,
    [switch]$NoThinking,
    [switch]$JsonObject,
    [ValidateSet("model-first", "rules-first")]
    [string]$PlanningMode = "model-first",
    [ValidateSet("unit", "normalized_1000")]
    [string]$CoordinateSpace = "normalized_1000",
    [string]$Record = "",
    [switch]$SkipNativeCapture
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

function Show-Usage {
    @"
Universal Game Agent - safe visual-game launcher

Required:
  -Profile <yaml>       Target process/window/control profile
  -Goal <text>          One concrete objective for the current run
  -Model <name>         Vision model exposed by the endpoint

Common options:
  -BaseUrl <url>        OpenAI-compatible endpoint (default: UGA_VLM_BASE_URL)
  -GoalEvidence <text>  Repeatable OCR completion evidence
  -DurationSeconds 300  Bounded run; use 0 only for supervised continuous use
  -Continuous           Continue with fresh cycles after permitted completion
  -NoThinking           Disable reasoning output on compatible providers
  -JsonObject           Use provider JSON-object response mode
  -Record <directory>   Record a replayable episode

Examples:
  .\scripts\run_game_agent.ps1 -Profile .\configs\games\my-game.yaml `
    -Goal 'Open the quest panel' -GoalEvidence 'Quest list' `
    -Model 'vision-model' -BaseUrl 'http://127.0.0.1:1234/v1'

  .\start.cmd -Profile .\configs\games\my-game.yaml `
    -Goal 'Advance the current objective' -Model 'vision-model' `
    -BaseUrl 'https://provider.example/v1' -DurationSeconds 0 -Continuous

Emergency stop: Ctrl+Shift+F12. Console stop: Ctrl+C.
Dashboard: http://127.0.0.1:<DashboardPort>
"@ | Write-Host
}

if ($Help) {
    Show-Usage
    exit 0
}

if ([string]::IsNullOrWhiteSpace($Profile)) { $Profile = $env:UGA_PROFILE }
if ([string]::IsNullOrWhiteSpace($Goal)) { $Goal = $env:UGA_GOAL }
if ([string]::IsNullOrWhiteSpace($Model)) { $Model = $env:UGA_VLM_MODEL }
if ([string]::IsNullOrWhiteSpace($BaseUrl)) { $BaseUrl = $env:UGA_VLM_BASE_URL }

$missing = @()
if ([string]::IsNullOrWhiteSpace($Profile)) { $missing += "-Profile" }
if ([string]::IsNullOrWhiteSpace($Goal)) { $missing += "-Goal" }
if ([string]::IsNullOrWhiteSpace($Model)) { $missing += "-Model" }
if ([string]::IsNullOrWhiteSpace($BaseUrl)) { $missing += "-BaseUrl" }
if ($missing.Count -gt 0) {
    Show-Usage
    throw "Missing required launcher values: $($missing -join ', ')"
}
if ($DashboardPort -lt 0 -or $DashboardPort -gt 65535) {
    throw "DashboardPort must be within [0, 65535]"
}
if ([string]::IsNullOrWhiteSpace($ApiKeyEnv)) {
    throw "ApiKeyEnv cannot be empty"
}

$profilePath = if ([System.IO.Path]::IsPathRooted($Profile)) {
    $Profile
}
else {
    Join-Path $projectRoot $Profile
}
if (-not (Test-Path -LiteralPath $profilePath -PathType Leaf)) {
    throw "Profile not found: $profilePath"
}
$profileText = Get-Content -LiteralPath $profilePath -Raw
if ($profileText -match 'CHANGE_ME') {
    throw "Profile still contains CHANGE_ME placeholders; copy and customize the example first"
}

$resolvedPython = $null
if (-not [string]::IsNullOrWhiteSpace($Python)) {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Python runtime not found: $Python"
    }
    $resolvedPython = (Resolve-Path -LiteralPath $Python).Path
}
else {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $resolvedPython = (Resolve-Path -LiteralPath $venvPython).Path
    }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -ne $pythonCommand) { $resolvedPython = $pythonCommand.Source }
    }
}
if ($null -eq $resolvedPython) {
    throw "Python was not found. Run install.cmd or pass -Python <path>"
}

if (-not $SkipNativeCapture) {
    $captureDll = Join-Path $projectRoot "native\target\release\uga_capture.dll"
    if (Test-Path -LiteralPath $captureDll -PathType Leaf) {
        $env:UGA_NATIVE_CAPTURE_DLL = (Resolve-Path -LiteralPath $captureDll).Path
        $env:UGA_NATIVE_CAPTURE_SHA256 = (
            Get-FileHash -LiteralPath $captureDll -Algorithm SHA256
        ).Hash.ToLowerInvariant()
    }
    else {
        Write-Warning "Native capture DLL not found; runtime may use a compatibility backend"
    }
}

$isLocalEndpoint = $BaseUrl -match '^https?://(?:127\.0\.0\.1|localhost)(?::|/)'
if (-not $isLocalEndpoint) {
    $apiKey = [Environment]::GetEnvironmentVariable($ApiKeyEnv, "Process")
    if ([string]::IsNullOrWhiteSpace($apiKey)) {
        $apiKey = [Environment]::GetEnvironmentVariable($ApiKeyEnv, "User")
    }
    if ([string]::IsNullOrWhiteSpace($apiKey)) {
        throw "Cloud endpoint requires API key environment variable $ApiKeyEnv"
    }
    [Environment]::SetEnvironmentVariable($ApiKeyEnv, $apiKey, "Process")
}

$env:PYTHONIOENCODING = "utf-8"
$agentArgs = @(
    "-m", "apps.agent", "run",
    "--profile", (Resolve-Path -LiteralPath $profilePath).Path,
    "--policy", "vlm",
    "--goal", $Goal,
    "--vlm-base-url", $BaseUrl,
    "--vlm-model", $Model,
    "--vlm-api-key-env", $ApiKeyEnv,
    "--duration-seconds", "$DurationSeconds",
    "--dashboard-port", "$DashboardPort",
    "--watchdog-timeout-seconds", "60",
    "--decision-timeout-seconds", "35",
    "--vlm-timeout-seconds", "15",
    "--execution-frame-age-ms", "30000",
    "--observation-hz", "2",
    "--capture-hz", "4",
    "--vision-mode", "auto",
    "--ocr", "auto",
    "--max-recoveries", "2",
    "--gui-planning-mode", $PlanningMode,
    "--gui-coordinate-space", $CoordinateSpace,
    "--vlm-image-width", "640",
    "--vlm-temporal-frames", "1",
    "--vlm-target-crops", "0",
    "--vlm-compact-output"
)
foreach ($evidence in $GoalEvidence) {
    if (-not [string]::IsNullOrWhiteSpace($evidence)) {
        $agentArgs += @("--goal-evidence", $evidence)
    }
}
if ($Continuous) { $agentArgs += "--continuous" }
if ($NoThinking) { $agentArgs += "--vlm-no-thinking" }
if ($JsonObject) { $agentArgs += "--vlm-json-object" }
if ($PlanningMode -eq "rules-first") { $agentArgs += "--vlm-ocr-task-fallback" }
if (-not [string]::IsNullOrWhiteSpace($Record)) {
    $recordPath = if ([System.IO.Path]::IsPathRooted($Record)) {
        $Record
    }
    else {
        Join-Path $projectRoot $Record
    }
    $agentArgs += @("--record", $recordPath)
}

Write-Host "[UGA] Profile: $((Resolve-Path -LiteralPath $profilePath).Path)"
Write-Host "[UGA] Goal: $Goal"
Write-Host "[UGA] Model endpoint: $BaseUrl"
Write-Host "[UGA] Emergency stop: Ctrl+Shift+F12"
if ($DashboardPort -gt 0) {
    Write-Host "[UGA] Dashboard: http://127.0.0.1:$DashboardPort"
}

Push-Location $projectRoot
try {
    & $resolvedPython @agentArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
