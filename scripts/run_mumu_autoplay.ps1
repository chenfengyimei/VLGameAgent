[CmdletBinding()]
param(
    [string]$Python = "C:\Users\cy\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe",
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
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python runtime not found: $Python"
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$toolingPath = Join-Path $projectRoot ".tooling"
$captureDll = Join-Path $projectRoot "native\target\release\uga_capture.dll"
$profilePath = Join-Path $projectRoot "configs\games\mumu-xianyu.yaml"
$logRoot = Join-Path $projectRoot "runs\live-agent"
$supervisorLog = Join-Path $logRoot "supervisor.log"

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

    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
    Add-Content -LiteralPath $supervisorLog -Value $line -Encoding utf8
    Write-Host $line
}

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
    while ($true) {
        try {
            Ensure-LocalModel
            break
        }
        catch {
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

$agentArgs = @(
    "-m", "apps.agent", "run",
    "--profile", $profilePath,
    "--policy", "vlm",
    "--goal", $goal,
    "--vlm-base-url", $BaseUrl,
    "--vlm-model", $Model,
    "--vlm-no-thinking",
    "--vlm-decision-interval", "3",
    "--vlm-timeout-seconds", "$VisionTimeoutSeconds",
    "--vlm-max-output-tokens", "256",
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
if (-not $useLocalModel) {
    $agentArgs += "--vlm-json-object"
}

Write-SupervisorLog "Starting supervisor loop (crash-restart + window rediscovery)"
# PS 5.1 wraps the agent's stderr lines (RapidOCR INFO logs) as
# NativeCommandError records; with ErrorActionPreference = Stop that kills
# the launcher on the first stderr line. Relax it around the agent pipe.
$ErrorActionPreference = "Continue"
while ($true) {
    Write-SupervisorLog "Starting one continuous UGA agent process"
    & $Python @agentArgs 2>&1 | ForEach-Object {
        $agentLine = "$_"
        Add-Content -LiteralPath $supervisorLog -Value $agentLine -Encoding utf8
        Write-Host $agentLine
    }
    $agentExitCode = $LASTEXITCODE
    Write-SupervisorLog "Continuous UGA agent exited with code $agentExitCode"
    if ($agentExitCode -eq 0) {
        # Clean exit means the user stopped the agent (Ctrl+Shift+F12).
        exit 0
    }
    # A crash (stale window HWND, backend failure, ...) self-heals here: the
    # fresh process re-discovers the MuMu window before continuing.
    Write-SupervisorLog "Restarting agent in $RestartDelaySeconds seconds after a crash"
    Start-Sleep -Seconds $RestartDelaySeconds
}
