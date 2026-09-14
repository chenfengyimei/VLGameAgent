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

function Invoke-BoundedProcess {
    param(
        [Parameter(Mandatory)]
        [string]$FilePath,
        [Parameter(Mandatory)]
        [string[]]$Arguments,
        [ValidateRange(100, 120000)]
        [int]$TimeoutMilliseconds = 15000,
        [switch]$AllowNonZeroExit
    )

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) {
        $startInfo.ArgumentList.Add($argument)
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "failed to start process: $FilePath"
    }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutMilliseconds)) {
        try {
            $process.Kill($true)
            $process.WaitForExit()
        } finally {
            $process.Dispose()
        }
        throw "process timed out after ${TimeoutMilliseconds}ms: $FilePath $($Arguments -join ' ')"
    }
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    $exitCode = $process.ExitCode
    $process.Dispose()
    if ($exitCode -ne 0 -and -not $AllowNonZeroExit) {
        throw "process failed with exit code ${exitCode}: $FilePath $($Arguments -join ' '): $stderr"
    }
    [pscustomobject]@{
        stdout = $stdout
        stderr = $stderr
        exit_code = $exitCode
    }
}

$tasks = @(
    [pscustomobject]@{
        id = "network-internet"
        intent = "android.settings.WIRELESS_SETTINGS"
        setup_markers = @("互联网", "SIM 卡")
        goal_evidence = @("添加网络")
        action_target = "互联网"
        goal = "Open 互联网. Complete only when the 互联网 page and 添加网络 are visible; then output DONE."
    },
    [pscustomobject]@{
        id = "network-sim"
        intent = "android.settings.WIRELESS_SETTINGS"
        setup_markers = @("互联网", "SIM 卡")
        goal_evidence = @("移动数据")
        action_target = "SIM 卡"
        goal = "Open SIM 卡. Complete only when the China Mobile GSM page and 移动数据 are visible; then output DONE."
    },
    [pscustomobject]@{
        id = "network-hotspot"
        intent = "android.settings.WIRELESS_SETTINGS"
        setup_markers = @("互联网", "热点和网络共享")
        goal_evidence = @("WLAN 热点")
        action_target = "热点和网络共享"
        goal = "Open 热点和网络共享. Complete only when that page and WLAN 热点 are visible; then output DONE. Do not toggle anything."
    },
    [pscustomobject]@{
        id = "internet-preferences"
        intent = "android.settings.WIFI_SETTINGS"
        setup_markers = @("添加网络", "网络偏好设置")
        goal_evidence = @("自动开启 WLAN")
        action_target = "网络偏好设置"
        goal = "Open 网络偏好设置. Complete only when that page and 自动开启 WLAN are visible; then output DONE. Do not toggle anything."
    },
    [pscustomobject]@{
        id = "battery-schedule"
        intent = "android.settings.BATTERY_SAVER_SETTINGS"
        setup_markers = @("使用省电模式", "设置时间表")
        goal_evidence = @("根据电量百分比")
        action_target = "设置时间表"
        goal = "Open 设置时间表. Complete only when 没有时间表 and 根据电量百分比 are visible; then output DONE. Do not select a schedule."
    },
    [pscustomobject]@{
        id = "storage-games"
        intent = "android.settings.INTERNAL_STORAGE_SETTINGS"
        setup_markers = @("存储空间管理器", "游戏")
        goal_evidence = @("仙遇")
        action_target = "游戏"
        goal = "Open 游戏. Complete only when the 游戏 storage page lists 仙遇 and its storage size; then output DONE. Do not open the app entry."
    },
    [pscustomobject]@{
        id = "accessibility-display-size"
        intent = "android.settings.ACCESSIBILITY_SETTINGS"
        setup_markers = @("显示大小和文字", "放大功能")
        goal_evidence = @("预览", "字体大小")
        action_target = "显示大小和文字"
        goal = "Open 显示大小和文字. Complete only when 预览 and 字体大小 are visible; then output DONE. Do not change any slider."
    },
    [pscustomobject]@{
        id = "accessibility-magnification"
        intent = "android.settings.ACCESSIBILITY_SETTINGS"
        setup_markers = @("显示大小和文字", "放大功能")
        goal_evidence = @("快速放大屏幕")
        action_target = "放大功能"
        goal = "Open 放大功能. Complete only when 快速放大屏幕 is visible; then output DONE. Do not toggle anything."
    },
    [pscustomobject]@{
        id = "location-app-permissions"
        intent = "android.settings.LOCATION_SOURCE_SETTINGS"
        setup_markers = @("应用位置信息权限", "位置信息服务")
        goal_evidence = @("一律允许", "仅在使用时允许")
        action_target = "应用位置信息权限"
        goal = "Open 应用位置信息权限. Complete only when 一律允许 and 仅在使用时允许 are visible; then output DONE. Do not change permissions."
    },
    [pscustomobject]@{
        id = "location-services"
        intent = "android.settings.LOCATION_SOURCE_SETTINGS"
        setup_markers = @("应用位置信息权限", "位置信息服务")
        goal_evidence = @("WLAN 扫描", "蓝牙扫描")
        action_target = "位置信息服务"
        goal = "Open 位置信息服务. Complete only when WLAN 扫描 and 蓝牙扫描 are visible; then output DONE. Do not toggle anything."
    },
    [pscustomobject]@{
        id = "observe-network-dashboard"
        intent = "android.settings.WIRELESS_SETTINGS"
        setup_markers = @("互联网", "热点和网络共享")
        goal_evidence = @("互联网", "热点和网络共享")
        goal = "The requested destination is already open. Complete only when 互联网 and 热点和网络共享 are both visible on the current page; output DONE without any physical action."
    },
    [pscustomobject]@{
        id = "observe-internet"
        intent = "android.settings.WIFI_SETTINGS"
        setup_markers = @("添加网络", "网络偏好设置")
        goal_evidence = @("添加网络", "网络偏好设置")
        goal = "The requested destination is already open. Complete only when 添加网络 and 网络偏好设置 are both visible; output DONE without any physical action."
    },
    [pscustomobject]@{
        id = "observe-battery-saver"
        intent = "android.settings.BATTERY_SAVER_SETTINGS"
        setup_markers = @("使用省电模式", "设置时间表")
        goal_evidence = @("使用省电模式", "设置时间表")
        goal = "The requested destination is already open. Complete only when 使用省电模式 and 设置时间表 are both visible; output DONE without any physical action."
    },
    [pscustomobject]@{
        id = "observe-storage"
        intent = "android.settings.INTERNAL_STORAGE_SETTINGS"
        setup_markers = @("存储空间管理器", "游戏")
        goal_evidence = @("存储空间管理器", "游戏")
        goal = "The requested destination is already open. Complete only when 存储空间管理器 and 游戏 are both visible; output DONE without any physical action."
    },
    [pscustomobject]@{
        id = "observe-display"
        intent = "android.settings.DISPLAY_SETTINGS"
        setup_markers = @("亮度", "锁定的屏幕")
        goal_evidence = @("亮度", "锁定的屏幕")
        goal = "The requested destination is already open. Complete only when 亮度 and 锁定的屏幕 are both visible; output DONE without any physical action."
    },
    [pscustomobject]@{
        id = "observe-accessibility"
        intent = "android.settings.ACCESSIBILITY_SETTINGS"
        setup_markers = @("显示大小和文字", "放大功能")
        goal_evidence = @("显示大小和文字", "放大功能")
        goal = "The requested destination is already open. Complete only when 显示大小和文字 and 放大功能 are both visible; output DONE without any physical action."
    },
    [pscustomobject]@{
        id = "observe-location"
        intent = "android.settings.LOCATION_SOURCE_SETTINGS"
        setup_markers = @("近期位置信息访问", "位置信息服务")
        goal_evidence = @("近期位置信息访问", "位置信息服务")
        goal = "The requested destination is already open. Complete only when 近期位置信息访问 and 位置信息服务 are both visible; output DONE without any physical action."
    },
    [pscustomobject]@{
        id = "observe-sound"
        intent = "android.settings.SOUND_SETTINGS"
        setup_markers = @("媒体音量", "通知音量")
        goal_evidence = @("媒体音量", "通知音量")
        goal = "The requested destination is already open. Complete only when 媒体音量 and 通知音量 are both visible; output DONE without any physical action."
    },
    [pscustomobject]@{
        id = "observe-language"
        intent = "android.settings.LOCALE_SETTINGS"
        setup_markers = @("首选语言顺序", "添加语言")
        goal_evidence = @("首选语言顺序", "添加语言")
        goal = "The requested destination is already open. Complete only when 首选语言顺序 and 添加语言 are both visible; output DONE without any physical action."
    },
    [pscustomobject]@{
        id = "observe-date-time"
        intent = "android.settings.DATE_SETTINGS"
        setup_markers = @("自动确定日期和时间", "自动确定时区")
        goal_evidence = @("自动确定日期和时间", "自动确定时区")
        goal = "The requested destination is already open. Complete only when 自动确定日期和时间 and 自动确定时区 are both visible; output DONE without any physical action."
    }
)

if ($tasks.Count -ne 20 -or @($tasks.id | Sort-Object -Unique).Count -ne 20) {
    throw "MuMu qualification matrix must contain exactly 20 unique goals"
}
if (@($tasks | Where-Object { $_.goal_evidence.Count -lt 1 }).Count -ne 0) {
    throw "every MuMu qualification goal must declare fresh OCR completion evidence"
}
if (@($tasks | Where-Object { $_.id -notlike "observe-*" -and -not $_.action_target }).Count -ne 0) {
    throw "every MuMu navigation goal must declare its single-step action target"
}

if ($DryRun) {
    $tasks | ConvertTo-Json -Depth 4
    exit 0
}

function Get-SettingsSetupSnapshot {
    param(
        [Parameter(Mandatory)]
        [string]$Adb,
        [Parameter(Mandatory)]
        [string]$Serial
    )

    try {
        $focusResult = Invoke-BoundedProcess `
            -FilePath $Adb `
            -Arguments @("-s", $Serial, "shell", "dumpsys", "window") `
            -TimeoutMilliseconds 10000
        $focus = ($focusResult.stdout -split "`r?`n" |
            Select-String "mCurrentFocus" | Select-Object -First 1).Line
    } catch {
        $focus = ""
    }
    # MuMu Android 15 often segfaults after emitting a complete hierarchy and
    # therefore may never materialize the requested /sdcard file. ADB exec-out
    # preserves the XML stream and reports a usable status despite that guest
    # process teardown, so validate the fresh stream instead of a stale file.
    try {
        $hierarchyResult = Invoke-BoundedProcess `
            -FilePath $Adb `
            -Arguments @("-s", $Serial, "exec-out", "uiautomator", "dump", "/dev/tty") `
            -TimeoutMilliseconds 15000 `
            -AllowNonZeroExit
        $hierarchy = $hierarchyResult.stdout
    } catch {
        $hierarchy = ""
    }
    if ($hierarchy -notmatch "<hierarchy") {
        $hierarchy = ""
    }
    [pscustomobject]@{ focus = $focus; hierarchy = $hierarchy }
}

function Start-VerifiedSettingsPage {
    param(
        [Parameter(Mandatory)]
        [string]$Adb,
        [Parameter(Mandatory)]
        [string]$Serial,
        [Parameter(Mandatory)]
        [pscustomobject]$Task
    )

    Invoke-BoundedProcess `
        -FilePath $Adb `
        -Arguments @("-s", $Serial, "shell", "am", "force-stop", "com.android.permissioncontroller") `
        -TimeoutMilliseconds 10000 | Out-Null
    Invoke-BoundedProcess `
        -FilePath $Adb `
        -Arguments @("-s", $Serial, "shell", "am", "force-stop", "com.android.settings") `
        -TimeoutMilliseconds 10000 | Out-Null
    $needsStart = $true
    $startAttempts = 0
    $lastSnapshot = $null
    for ($hierarchyAttempt = 1; $hierarchyAttempt -le 5; $hierarchyAttempt++) {
        if ($needsStart) {
            if ($startAttempts -ge 3) {
                break
            }
            $startAttempts++
            try {
                Invoke-BoundedProcess `
                    -FilePath $Adb `
                    -Arguments @("-s", $Serial, "shell", "am", "start", "-W", "-a", $Task.intent) `
                    -TimeoutMilliseconds 15000 | Out-Null
            } catch {
                Write-Warning "bounded Settings start failed for $($Task.id): $_"
                continue
            }
        }
        Start-Sleep -Milliseconds 1200
        $lastSnapshot = Get-SettingsSetupSnapshot -Adb $Adb -Serial $Serial
        $markersReady = @(
            $Task.setup_markers | Where-Object {
                $lastSnapshot.hierarchy -notmatch [regex]::Escape($_)
            }
        ).Count -eq 0
        if ($lastSnapshot.focus -match "com\.android\.settings" -and $markersReady) {
            # Allow the compositor to publish the verified hierarchy before WGC attaches.
            Start-Sleep -Milliseconds 500
            return
        }
        # An empty hierarchy is a transient MuMu UIAutomator failure. Preserve
        # the verified Settings activity across five bounded dump retries;
        # restarting it here would reset UIAutomator to the same unstable startup
        # window. Activity starts retain their separate three-attempt ceiling.
        $needsStart = $lastSnapshot.focus -notmatch "com\.android\.settings"
        Start-Sleep -Milliseconds 500
    }
    $missing = @(
        $Task.setup_markers | Where-Object {
            $null -eq $lastSnapshot -or
            $lastSnapshot.hierarchy -notmatch [regex]::Escape($_)
        }
    ) -join ", "
    $focus = if ($null -eq $lastSnapshot) { "unavailable" } else { $lastSnapshot.focus }
    throw "Settings setup did not stabilize for $($Task.id): focus=$focus missing=[$missing]"
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
    Invoke-BoundedProcess `
        -FilePath $AdbExecutable `
        -Arguments @("connect", $AdbSerial) `
        -TimeoutMilliseconds 10000 | Out-Null
    $adbState = Invoke-BoundedProcess `
        -FilePath $AdbExecutable `
        -Arguments @("-s", $AdbSerial, "get-state") `
        -TimeoutMilliseconds 10000
    if ($adbState.stdout.Trim() -ne "device") {
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

            Start-VerifiedSettingsPage `
                -Adb $AdbExecutable `
                -Serial $AdbSerial `
                -Task $task

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
                "--duration-seconds", "75",
                "--observation-hz", "5",
                "--dashboard-port", "0",
                "--record", $episodes,
                "--qualification-project-root", $project
            )
            foreach ($requiredEvidence in $task.goal_evidence) {
                $arguments += @("--goal-evidence", $requiredEvidence)
            }
            if ($null -ne $task.action_target) {
                $arguments += @("--goal-action-target", $task.action_target)
            }
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
