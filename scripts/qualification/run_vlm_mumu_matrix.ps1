param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")),
    [string]$EvidenceRoot = "runs/qualification-vlm/live-v1",
    [string]$PythonExecutable = "python",
    [string]$AdbExecutable = "D:\MuMu\MuMuPlayer\nx_device\15.0\shell\adb.exe",
    [string]$AdbSerial = "127.0.0.1:16416",
    [string]$Model = "qwen3-vl-4b-instruct",
    [string]$BaseUrl = "http://127.0.0.1:1234/v1",
    [ValidateRange(1, 5)]
    [int]$Repetitions = 5,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$tasks = @(
    [pscustomobject]@{
        id = "network-internet"
        intent = "android.settings.WIRELESS_SETTINGS"
        goal = "Open 互联网. Complete only when the 互联网 page and 添加网络 are visible; then output DONE."
    },
    [pscustomobject]@{
        id = "network-sim"
        intent = "android.settings.WIRELESS_SETTINGS"
        goal = "Open SIM 卡. Complete only when the China Mobile GSM page and 移动数据 are visible; then output DONE."
    },
    [pscustomobject]@{
        id = "network-hotspot"
        intent = "android.settings.WIRELESS_SETTINGS"
        goal = "Open 热点和网络共享. Complete only when that page and WLAN 热点 are visible; then output DONE. Do not toggle anything."
    },
    [pscustomobject]@{
        id = "network-data-saver"
        intent = "android.settings.WIRELESS_SETTINGS"
        goal = "Open 省流模式. Complete only when that page and 启用省流模式 are visible; then output DONE. Do not toggle anything."
    },
    [pscustomobject]@{
        id = "network-vpn"
        intent = "android.settings.WIRELESS_SETTINGS"
        goal = "Open VPN. Complete only when the VPN page and 尚未添加任何 VPN are visible; then output DONE."
    },
    [pscustomobject]@{
        id = "internet-preferences"
        intent = "android.settings.WIFI_SETTINGS"
        goal = "Open 网络偏好设置. Complete only when that page and 自动开启 WLAN are visible; then output DONE. Do not toggle anything."
    },
    [pscustomobject]@{
        id = "connected-preferences"
        intent = "android.settings.BLUETOOTH_SETTINGS"
        goal = "Open 连接偏好设置. Complete only when that page and 投屏 are visible; then output DONE. Do not enable Bluetooth."
    },
    [pscustomobject]@{
        id = "apps-browser-info"
        intent = "android.settings.APPLICATION_SETTINGS"
        goal = "Open the 浏览器 app information page. Complete only when 浏览器, 归档, and 强行停止 are visible; then output DONE. Do not press those controls."
    },
    [pscustomobject]@{
        id = "apps-settings-info"
        intent = "android.settings.APPLICATION_SETTINGS"
        goal = "Open the 设置 app information page. Complete only when 设置, 归档, and 强行停止 are visible; then output DONE. Do not press those controls."
    },
    [pscustomobject]@{
        id = "apps-xianyu-info"
        intent = "android.settings.APPLICATION_SETTINGS"
        goal = "Open the 仙遇 app information page. Complete only when 仙遇, 卸载, and 强行停止 are visible; then output DONE. Do not press those controls."
    },
    [pscustomobject]@{
        id = "display-lock-screen"
        intent = "android.settings.DISPLAY_SETTINGS"
        goal = "Open 锁定的屏幕. Complete only when that page and 要显示的内容 are visible; then output DONE. Do not change any option."
    },
    [pscustomobject]@{
        id = "accessibility-display-size"
        intent = "android.settings.ACCESSIBILITY_SETTINGS"
        goal = "Open 显示大小和文字. Complete only when 预览 and 字体大小 are visible; then output DONE. Do not change any slider."
    },
    [pscustomobject]@{
        id = "accessibility-color-motion"
        intent = "android.settings.ACCESSIBILITY_SETTINGS"
        goal = "Open 色彩和动画. Complete only when 颜色反转 and 移除动画 are visible; then output DONE. Do not toggle anything."
    },
    [pscustomobject]@{
        id = "accessibility-magnification"
        intent = "android.settings.ACCESSIBILITY_SETTINGS"
        goal = "Open 放大功能. Complete only when 快速放大屏幕 and 放大功能快捷方式 are visible; then output DONE. Do not toggle anything."
    },
    [pscustomobject]@{
        id = "location-app-permissions"
        intent = "android.settings.LOCATION_SOURCE_SETTINGS"
        goal = "Open 应用位置信息权限. Complete only when 一律允许 and 仅在使用时允许 are visible; then output DONE. Do not change permissions."
    },
    [pscustomobject]@{
        id = "location-services"
        intent = "android.settings.LOCATION_SOURCE_SETTINGS"
        goal = "Open 位置信息服务. Complete only when WLAN 扫描 and 蓝牙扫描 are visible; then output DONE. Do not toggle anything."
    },
    [pscustomobject]@{
        id = "browser-notifications"
        intent = "android.settings.APPLICATION_SETTINGS"
        goal = 'Open 浏览器, then open 通知. Complete only when 所有“浏览器”通知 and 常规 are visible; then output DONE. Do not toggle notifications or press archive, disable, or force stop.'
    },
    [pscustomobject]@{
        id = "browser-permissions"
        intent = "android.settings.APPLICATION_SETTINGS"
        goal = "Open 浏览器, then open 权限. Complete only when the 应用权限 page shows 浏览器, 已允许, and 位置信息; then output DONE. Do not change permissions."
    },
    [pscustomobject]@{
        id = "location-recent-access"
        intent = "android.settings.LOCATION_SOURCE_SETTINGS"
        goal = "Open 查看全部. Complete only when 近期位置信息访问 and 最近没有任何应用申请使用位置信息 are visible; then output DONE."
    },
    [pscustomobject]@{
        id = "accessibility-lawnchair"
        intent = "android.settings.ACCESSIBILITY_SETTINGS"
        goal = 'Open Lawnchair. Complete only when the Lawnchair page and 使用“Lawnchair” are visible; then output DONE. Do not enable the service.'
    }
)

if ($DryRun) {
    $tasks | ConvertTo-Json -Depth 4
    exit 0
}

$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$evidence = if ([IO.Path]::IsPathRooted($EvidenceRoot)) {
    [IO.Path]::GetFullPath($EvidenceRoot)
} else {
    [IO.Path]::GetFullPath((Join-Path $project $EvidenceRoot))
}
$episodes = Join-Path $evidence "episodes"
$planPath = Join-Path $evidence "plan-draft.json"
$planTemp = Join-Path $evidence "plan-draft.tmp"
$runLog = Join-Path $evidence "matrix-run.log"
New-Item -ItemType Directory -Path $episodes -Force | Out-Null

Push-Location $project
try {
    $revision = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $revision -notmatch "^[0-9a-f]{40}$") {
        throw "cannot resolve a full Git source revision"
    }
    if ((& git status --porcelain).Count -ne 0) {
        throw "qualification requires a clean Git checkout"
    }
    if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf) -and
        -not (Get-Command $PythonExecutable -ErrorAction SilentlyContinue)) {
        throw "Python executable is unavailable: $PythonExecutable"
    }
    if (-not (Test-Path -LiteralPath $AdbExecutable -PathType Leaf)) {
        throw "ADB executable is unavailable: $AdbExecutable"
    }
    & $AdbExecutable connect $AdbSerial | Out-Null
    if ((& $AdbExecutable -s $AdbSerial get-state).Trim() -ne "device") {
        throw "MuMu ADB target is not ready: $AdbSerial"
    }
    $models = Invoke-RestMethod -Uri ($BaseUrl.TrimEnd("/") + "/models") -TimeoutSec 10
    if ($Model -notin @($models.data.id)) {
        throw "vision model is not exposed by the endpoint: $Model"
    }

    $rows = @()
    if (Test-Path -LiteralPath $planPath -PathType Leaf) {
        $existing = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
        if ($existing.schema -ne "uga.vlm_live_plan" -or
            $existing.schema_version -ne "1.1" -or
            $existing.source_revision -ne $revision) {
            throw "existing live plan does not match the current source revision"
        }
        $rows = @($existing.episodes)
    }

    $nativeDll = (Resolve-Path -LiteralPath "native/target/release/uga_capture.dll").Path
    $env:UGA_NATIVE_CAPTURE_DLL = $nativeDll
    $env:UGA_NATIVE_CAPTURE_SHA256 = (
        Get-FileHash -LiteralPath $nativeDll -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $env:PYTHONPATH = "$project;$project\.tooling"
    $consecutiveFailures = 0

    foreach ($task in $tasks) {
        for ($repetition = 0; $repetition -lt $Repetitions; $repetition++) {
            $alreadyRecorded = @(
                $rows | Where-Object {
                    $_.role -eq "task" -and
                    $_.goal_id -eq $task.id -and
                    [int]$_.repetition -eq $repetition
                }
            )
            if ($alreadyRecorded.Count -gt 0) {
                Write-Host "[skip] $($task.id) repetition=$repetition"
                continue
            }

            & $AdbExecutable -s $AdbSerial shell am force-stop com.android.settings | Out-Null
            & $AdbExecutable -s $AdbSerial shell am start -W -a $task.intent | Out-Null
            Start-Sleep -Milliseconds 750
            $focus = (& $AdbExecutable -s $AdbSerial shell dumpsys window |
                Select-String "mCurrentFocus" | Select-Object -First 1).Line
            if ($focus -notmatch "com\.android\.settings") {
                throw "Settings setup did not become foreground for $($task.id): $focus"
            }

            $before = @(
                Get-ChildItem -LiteralPath $episodes -Directory | ForEach-Object Name
            )
            $arguments = @(
                "-m", "apps.agent", "run",
                "--profile", "configs/games/mumu-xianyu.yaml",
                "--policy", "vlm",
                "--goal", $task.goal,
                "--vlm-base-url", $BaseUrl,
                "--vlm-model", $Model,
                "--vlm-no-thinking",
                "--vlm-max-output-tokens", "768",
                "--vlm-decision-interval", "1",
                "--vlm-timeout-seconds", "30",
                "--vision-mode", "local",
                "--ocr", "auto",
                "--max-recoveries", "2",
                "--duration-seconds", "45",
                "--observation-hz", "5",
                "--dashboard-port", "0",
                "--record", $episodes,
                "--qualification-project-root", $project
            )
            Write-Host "[run] $($task.id) repetition=$repetition"
            & $PythonExecutable @arguments 2>&1 |
                Tee-Object -FilePath $runLog -Append |
                Write-Host
            if ($LASTEXITCODE -ne 0) {
                throw "agent process failed for $($task.id) repetition=$repetition"
            }
            $after = @(
                Get-ChildItem -LiteralPath $episodes -Directory | ForEach-Object Name
            )
            $created = @($after | Where-Object { $_ -notin $before })
            if ($created.Count -ne 1) {
                throw "agent run did not create exactly one Episode directory"
            }
            $runPath = Join-Path (Join-Path $episodes $created[0]) "run.json"
            $run = Get-Content -LiteralPath $runPath -Raw | ConvertFrom-Json
            if ($run.source_revision -ne $revision -or $run.source_tree_clean -ne $true) {
                throw "Episode source binding is invalid: $($created[0])"
            }
            $rows += [pscustomobject]@{
                episode_id = $created[0]
                goal_id = $task.id
                repetition = $repetition
                role = "task"
                reviewed = $false
                wrong_window = $false
                wrong_target = $false
                critical_error = $false
                loop_detection_rounds = $null
            }
            $plan = [ordered]@{
                schema = "uga.vlm_live_plan"
                schema_version = "1.1"
                source_revision = $revision
                episodes = @($rows)
            }
            $json = $plan | ConvertTo-Json -Depth 6
            [IO.File]::WriteAllText($planTemp, $json + [Environment]::NewLine)
            Move-Item -LiteralPath $planTemp -Destination $planPath -Force

            if ($run.result -eq "success" -and $run.termination_reason -eq "goal_confirmed") {
                $consecutiveFailures = 0
            } else {
                $consecutiveFailures++
                Write-Warning "Episode failed: $($created[0]) reason=$($run.termination_reason)"
                if ($consecutiveFailures -ge 2) {
                    throw "two consecutive task Episodes failed; stopping the matrix"
                }
            }
        }
    }
    Write-Host "Draft plan: $planPath"
} finally {
    Pop-Location
}
