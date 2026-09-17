[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$Model = "glm-4.6v",
    [string]$BaseUrl = "https://open.bigmodel.cn/api/paas/v4",
    [int]$DashboardPort = 8787,
    [int]$RestartDelaySeconds = 15,
    [int]$ModelContextLength = 8192,
    [int]$VisionTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

if ($DashboardPort -lt 1 -or $DashboardPort -gt 65535) {
    throw "DashboardPort must be within [1, 65535]"
}
if ($RestartDelaySeconds -lt 1) {
    throw "RestartDelaySeconds must be positive"
}
if ($ModelContextLength -lt 2048) {
    throw "ModelContextLength must be at least 2048"
}
if ($VisionTimeoutSeconds -lt 30) {
    throw "VisionTimeoutSeconds must be at least 30"
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$toolingPath = Join-Path $projectRoot ".tooling"
$captureDll = Join-Path $projectRoot "native\target\release\uga_capture.dll"
$profilePath = Join-Path $projectRoot "configs\games\mumu-xianyu.yaml"
$logRoot = Join-Path $projectRoot "runs\live-agent"
$supervisorLog = Join-Path $logRoot "supervisor.log"

# D13: no user-specific Python default — resolve explicitly (param, project
# venv, then PATH) so the script is portable across machines and accounts.
$resolvedPython = $null
if (-not [string]::IsNullOrWhiteSpace($Python)) {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Python runtime not found: $Python"
    }
    $resolvedPython = $Python
}
else {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $resolvedPython = $venvPython
    }
    else {
        $fromPath = Get-Command python -ErrorAction SilentlyContinue
        if ($null -ne $fromPath) {
            $resolvedPython = $fromPath.Source
        }
    }
    if ($null -eq $resolvedPython) {
        throw "No Python runtime resolved; pass -Python <path> explicitly"
    }
}

foreach ($requiredPath in ($toolingPath, $captureDll, $profilePath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required runtime path not found: $requiredPath"
    }
}

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

$env:PYTHONPATH = $toolingPath
$env:PYTHONIOENCODING = "utf-8"
$env:UGA_NATIVE_CAPTURE_DLL = (Resolve-Path -LiteralPath $captureDll).Path
$env:UGA_NATIVE_CAPTURE_SHA256 = `
    (Get-FileHash -LiteralPath $captureDll -Algorithm SHA256).Hash.ToLowerInvariant()

$goal = @"
持续自主推进游戏进度。以画面左上角任务追踪显示的当前主线任务为准，点击具体任务文字自动寻路；引导手势/光圈点其指向位置；NPC 对话点继续，多选项点任务相关的选项；养成任务进对应界面完成一次操作即可；提示/奖励弹窗点确定、领取或关闭；功能页完成后点左上角的返回花纹退出。充值、首充、礼包、特惠类弹窗一律不点。点过没反应的按钮不要重复点。
"@.Trim()

function Write-SupervisorLog {
    param([Parameter(Mandatory = $true)][string]$Message)

    # D13: rotate the supervisor log before it grows without bound.
    if ((Test-Path -LiteralPath $supervisorLog) -and ((Get-Item -LiteralPath $supervisorLog).Length -gt 5MB)) {
        Move-Item -LiteralPath $supervisorLog -Destination "$supervisorLog.1" -Force
    }
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
    Add-Content -LiteralPath $supervisorLog -Value $line -Encoding utf8
    Write-Host $line
}

Write-SupervisorLog "Resolved Python runtime: $resolvedPython"

function Ensure-LocalModel {
    $modelReady = $false
    try {
        $models = (Invoke-RestMethod -Uri "http://127.0.0.1:1234/v1/models" -TimeoutSec 5).data
        $modelReady = $null -ne ($models | Where-Object { $_.id -eq $Model })
    }
    catch {
        Write-SupervisorLog "LM Studio endpoint is unavailable; starting the local server"
        & lms server start | ForEach-Object { Write-SupervisorLog "lms: $_" }
        if ($LASTEXITCODE -ne 0) {
            throw "LM Studio server failed to start with exit code $LASTEXITCODE"
        }
    }

    $loadedModels = (& lms ps 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to query loaded LM Studio models"
    }
    if ($loadedModels -notmatch [regex]::Escape($Model)) {
        Write-SupervisorLog "Loading local model $Model"
        & lms load $Model `
            --identifier $Model `
            --gpu max `
            --context-length $ModelContextLength `
            --parallel 1 `
            --yes | ForEach-Object { Write-SupervisorLog "lms: $_" }
        if ($LASTEXITCODE -ne 0) {
            throw "LM Studio model failed to load with exit code $LASTEXITCODE"
        }
        $modelReady = $true
    }

    if (-not $modelReady) {
        $models = (Invoke-RestMethod -Uri "http://127.0.0.1:1234/v1/models" -TimeoutSec 10).data
        if ($null -eq ($models | Where-Object { $_.id -eq $Model })) {
            throw "LM Studio API does not expose model $Model"
        }
    }
}

$useLocalModel = $BaseUrl -match '^https?://(?:127\.0\.0\.1|localhost)(?::|/)'
if ($useLocalModel) {
    $readinessAttempts = 0
    while ($true) {
        try {
            Ensure-LocalModel
            break
        }
        catch {
            $readinessAttempts += 1
            if ($readinessAttempts -ge 5) { throw "Local model startup retry budget exhausted" }
            Write-SupervisorLog "Model readiness failed: $($_.Exception.Message)"
            Write-SupervisorLog "Retrying model startup in $RestartDelaySeconds seconds"
            Start-Sleep -Seconds $RestartDelaySeconds
        }
    }
}
else {
    if ([string]::IsNullOrWhiteSpace($env:UGA_VLM_API_KEY)) {
        $env:UGA_VLM_API_KEY = [Environment]::GetEnvironmentVariable(
            'UGA_VLM_API_KEY',
            'User'
        )
    }
    if ([string]::IsNullOrWhiteSpace($env:UGA_VLM_API_KEY)) {
        throw 'UGA_VLM_API_KEY is required for the cloud vision endpoint'
    }
}

$mandatoryThinking = $Model -match '(?i)(^|/)glm-5\.3(-flash)?$'
$outputBudget = if ($mandatoryThinking) { 4096 } else { 256 }
$agentArgs = @(
    "-m", "apps.agent", "run",
    "--profile", $profilePath,
    "--policy", "vlm",
    "--goal", $goal,
    "--vlm-base-url", $BaseUrl,
    "--vlm-model", $Model,
    "--vlm-decision-interval", "3",
    "--vlm-timeout-seconds", "$VisionTimeoutSeconds",
    "--vlm-max-output-tokens", "$outputBudget",
    "--decision-timeout-seconds", "$([Math]::Max(90, $VisionTimeoutSeconds))",
    "--vlm-temporal-frames", "1",
    "--vlm-image-width", "640",
    "--vlm-target-crops", "0",
    "--vlm-compact-output",
    "--vlm-ocr-task-fallback",
    "--vision-mode", "local",
    "--ocr", "auto",
    "--max-recoveries", "2",
    "--duration-seconds", "0",
    "--continuous",
    "--observation-hz", "1",
    "--capture-hz", "2",
    "--dashboard-port", "$DashboardPort"
)
if ($mandatoryThinking) {
    $agentArgs += @("--vlm-extra-body", '{"thinking":{"type":"enabled"},"reasoning_effort":"low"}')
}
else {
    $agentArgs += "--vlm-no-thinking"
}
if (-not $useLocalModel) {
    $agentArgs += "--vlm-json-object"
}

Write-SupervisorLog "Starting supervisor loop (crash-restart + window rediscovery)"
# PS 5.1 wraps the agent's stderr lines (RapidOCR INFO logs) as
# NativeCommandError records; with ErrorActionPreference = Stop that kills
# the launcher on the first stderr line. Relax it around the agent pipe.
$ErrorActionPreference = "Continue"
# F06: crash restarts are bounded — exponential backoff with jitter, a hard
# attempt cap, and the budget only resets after a long healthy run.
$maxRestarts = 10
$restartCount = 0
$totalRestarts = 0
$delaySeconds = $RestartDelaySeconds
while ($true) {
    Write-SupervisorLog "Starting one continuous UGA agent process"
    $startedAt = Get-Date
    & $resolvedPython @agentArgs 2>&1 | ForEach-Object {
        $agentLine = "$_"
        Write-SupervisorLog $agentLine
    }
    $agentExitCode = $LASTEXITCODE
    $ranSeconds = [int]((Get-Date) - $startedAt).TotalSeconds
    Write-SupervisorLog "Continuous UGA agent exited with code $agentExitCode after ${ranSeconds}s"
    if ($agentExitCode -eq 78) {
        Write-SupervisorLog "Fatal provider failure: automatic restart is disabled"
        exit 78
    }
    if ($agentExitCode -eq 0) {
        # Clean exit means the user stopped the agent (Ctrl+Shift+F12).
        exit 0
    }
    if ($ranSeconds -ge 300) {
        # A crash after a long healthy run is a fresh failure, not part of
        # the previous crash loop: reset the restart budget.
        if ($restartCount -gt 0) {
            Write-SupervisorLog "Agent ran ${ranSeconds}s before crashing; restarting the crash budget"
        }
        $restartCount = 0
        $delaySeconds = $RestartDelaySeconds
    }
    $restartCount += 1
    $totalRestarts += 1
    if ($totalRestarts -gt $maxRestarts) {
        Write-SupervisorLog "Total run restart budget exhausted"
        exit 1
    }
    if ($restartCount -gt $maxRestarts) {
        Write-SupervisorLog "Restart budget exhausted ($maxRestarts consecutive crashes); supervisor standing down"
        exit 1
    }
    $jitter = Get-Random -Minimum 0 -Maximum 3
    Write-SupervisorLog "Restarting agent in $delaySeconds seconds after a crash (attempt $restartCount/$maxRestarts)"
    Start-Sleep -Seconds ($delaySeconds + $jitter)
    $delaySeconds = [Math]::Min($delaySeconds * 2, 300)
}
