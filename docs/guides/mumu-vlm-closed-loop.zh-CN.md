# MuMu + 本地视觉模型闭环使用教程

本文面向第一次运行 UGA 视觉闭环的 Windows 用户。完成后，你将能让项目持续捕获
MuMu 窗口，由本地 Qwen3-VL 判断当前页面，只在证据充分时执行一个 GUI 动作，并在
浏览器里查看实时画面、决策、OCR 证据、动作效果和停止原因。

> 安全边界：请只操作你拥有或明确获准测试的应用。第一次运行建议使用 Android
> “设置”中的只读页面。任何时候按 `Ctrl+Shift+F12` 都会撤销控制租约、清空动作队列
> 并释放按键。

## 1. 工作方式

GUI 路径是有界闭环，不会让模型一次生成一串点击：

`持续捕获 → OCR + VLM 感知 → 单步决策 → 新鲜度/窗口/目标复核 → 执行一次 → 效果复核`

- `ACT` 最多包含一个目标标签和目标框，最终落点由运行时从最新帧计算。
- `WAIT`、`DONE`、`ABSTAIN` 不产生鼠标或键盘输入。
- `DONE` 只有在两张间隔至少 500ms 的新鲜帧上都找到必需 OCR 证据后才成立。
- 同一动作首次无效时最多重新定位重试一次；恢复最多两种，仍无进展就安全阻断。
- 推理期间 WGC/GDI 继续采集，返回的旧帧、旧窗口、旧几何和旧任务结果会被丢弃。

## 2. 安装环境

推荐使用 Windows 10/11、64 位 Python 3.12、Git 和 LM Studio。进入仓库后执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install --require-hashes -r requirements-lock.txt
python -m pip install --require-hashes -r requirements-vision-lock.txt
python -m pip install --no-deps -e .
```

验证命令入口与 OCR：

```powershell
uga-agent smoke
python -c "from rapidocr import RapidOCR; print('RapidOCR OK')"
```

如果 PowerShell 禁止激活虚拟环境，可在当前窗口临时执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

## 3. 构建并锚定原生捕获 DLL

普通发布包已经携带 DLL；源码运行需要安装 Rust 后构建一次：

```powershell
Push-Location native
cargo build --release
Pop-Location

$env:UGA_NATIVE_CAPTURE_DLL = (Resolve-Path native\target\release\uga_capture.dll).Path
$env:UGA_NATIVE_CAPTURE_SHA256 = `
  (Get-FileHash $env:UGA_NATIVE_CAPTURE_DLL -Algorithm SHA256).Hash.ToLowerInvariant()
```

这两个环境变量必须在运行 `uga-agent` 的同一 PowerShell 窗口里存在。摘要不匹配时程序
会拒绝加载 DLL；这是供应链保护，不应绕过。

## 4. 启动本地模型

1. 在 LM Studio 中加载支持图像输入的 `qwen3-vl-4b-instruct`。
2. 启动 OpenAI 兼容本地服务，默认地址为 `http://127.0.0.1:1234/v1`。
3. 验证服务：

```powershell
(Invoke-RestMethod http://127.0.0.1:1234/v1/models).data.id
```

输出中应包含稍后传给 `--vlm-model` 的精确模型 ID。4B 模型可能偶尔不遵守 JSON
Schema；UGA 会先探测结构化输出能力，只允许一次格式修复，失败后选择 `ABSTAIN`，
不会用正则猜测点击。

## 5. 准备 MuMu

1. 启动一个 MuMu 安卓设备窗口并保持完整可见。
2. 确认只有一个窗口标题匹配 `^MuMu安卓设备-\d+$`。
3. 不要让 MuMu 管理器或其他窗口覆盖设备窗口。
4. 运行时不要移动窗口、切换 DPI 或抢占焦点。

可选：用 MuMu 自带 ADB 打开一个只读设置页。请按实际安装路径调整 `$adb`：

```powershell
$adb = 'D:\MuMu\MuMuPlayer\nx_device\15.0\shell\adb.exe'
& $adb -s 127.0.0.1:16416 shell am start -W -a android.settings.DATE_SETTINGS
```

这条命令只打开“日期和时间”页面，不修改任何系统设置。

## 6. 第一次运行：零输入观察任务

先验证“看懂并停止”，不要立即测试点击：

```powershell
uga-agent run `
  --profile configs/games/mumu-xianyu.yaml `
  --policy vlm `
  --goal '当前已经是日期和时间页面；看到自动日期和自动时区后完成，禁止任何操作。' `
  --goal-evidence '自动确定日期和时间' `
  --goal-evidence '自动确定时区' `
  --vlm-base-url http://127.0.0.1:1234/v1 `
  --vlm-model qwen3-vl-4b-instruct `
  --vlm-no-thinking `
  --vlm-decision-interval 1 `
  --vlm-timeout-seconds 30 `
  --vision-mode local `
  --ocr auto `
  --max-recoveries 2 `
  --duration-seconds 45 `
  --observation-hz 5 `
  --dashboard-port 8787 `
  --record runs/episodes
```

立刻在浏览器打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)。面板仅绑定本机
回环地址、只读且不缓存画面。正常结果是状态变为“目标已确认”，控制台输出
`physical actions executed: 0`。

## 7. 单步导航任务

只有当初始页上的目标标签和目标页的完成证据都可明确描述时，才提供
`--goal-action-target`。例如先打开网络设置首页：

```powershell
& $adb -s 127.0.0.1:16416 shell am start -W -a android.settings.WIRELESS_SETTINGS
```

然后运行：

```powershell
uga-agent run `
  --profile configs/games/mumu-xianyu.yaml `
  --policy vlm `
  --goal '打开互联网；只有看到互联网页面和添加网络时才完成。' `
  --goal-action-target '互联网' `
  --goal-evidence '添加网络' `
  --vlm-base-url http://127.0.0.1:1234/v1 `
  --vlm-model qwen3-vl-4b-instruct `
  --vlm-no-thinking `
  --vlm-decision-interval 1 `
  --vision-mode local `
  --ocr auto `
  --max-recoveries 2 `
  --duration-seconds 75 `
  --observation-hz 5 `
  --dashboard-port 8787 `
  --record runs/episodes
```

一次逻辑点击通常对应三个物理事件：移动、按下、抬起。因此面板显示“逻辑动作 1、
物理事件 3”是正常的。模型若在到达目标页后仍建议继续点击，监督器会因完成证据已出现
而拒绝物理输入，并用第二张新鲜帧确认完成。

## 8. 如何读运行面板

| 面板项 | 正常含义 | 需要处理的信号 |
|---|---|---|
| 状态 | 运行中 / 目标已确认 | 已安全阻断表示证据不足或无安全动作；先看时间线，不要盲目重跑 |
| 最新决策 | `click`、`wait`、`done`、`abstain` | `WAIT/DONE/ABSTAIN` 下物理事件继续增长属于错误 |
| 目标/证据置信度 | 完成时应至少 0.85 | 低于门槛会继续观察或停止，不应降低门槛补偿 |
| 采集间隔 | 正式门槛 P95 ≤ 350ms、最大 ≤ 500ms | 超标先检查遮挡、GPU、WGC 与 GDI 回退 |
| 过期推理丢弃 | 可以大于 0，说明旧结果被安全拒绝 | 旧结果若对应物理动作则不合格 |
| 逻辑动作 / 物理事件 | 单击通常是 1 / 3 | 同一无效逻辑动作不得连续超过 2 次 |
| 恢复次数 | 0 最常见，最多 2 | 达到上限后必须停止 |

时间线中的“监督说明”比模型回复更重要：它记录动作为什么被执行、重新观察或拒绝。
长模型输出只显示摘要，完整数据保留在页面底部的“诊断原始数据”和 Episode 的
`planner.jsonl` 中。

## 9. 检查 Episode

运行结束会打印 Episode 路径。也可以自动找到最新一条：

```powershell
$episode = Get-ChildItem runs\episodes -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

Get-Content ($episode.FullName + '\run.json')
Get-Content ($episode.FullName + '\metrics.json')
Get-Content ($episode.FullName + '\planner.jsonl') -Tail 5
uga-replay $episode.FullName
```

成功任务应满足：

- `run.json` 的 `result` 为 `success`，`termination_reason` 为 `goal_confirmed`。
- 观察任务 `logical_actions_issued`、`scheduled_actions`、`executed_actions` 都为 0。
- 点击任务 `scheduled_actions == executed_actions == 3 * logical_actions_issued`。
- `pending_action_at_termination` 为 0，`recovery_count` 不超过 2。
- `planner.jsonl` 最后一条 `terminal` 事件包含完成证据置信度和明确终止原因。

`timeout`、用户停止、运行异常和恢复耗尽都不是成功；即使进程正常退出也不能改写结果。

## 10. 本地与混合验证模式

- `--vision-mode local`：只使用主 VLM；证据不足时安全停止。
- `--vision-mode auto`：按配置自动选择本地或混合路径。
- `--vision-mode hybrid`：在中置信度、OCR/VLM 冲突、未知页面或关键动作时调用验证器。

验证器示例：

```powershell
$env:UGA_VERIFIER_API_KEY = '<仅在当前终端设置>'
# 在前面的 uga-agent run 命令后追加：
--vision-mode hybrid `
--verifier-base-url https://example.invalid/v1 `
--verifier-model your-larger-vision-model `
--verifier-api-key-env UGA_VERIFIER_API_KEY
```

不要把真实密钥写进 YAML、命令历史、日志或 Episode。未配置验证器时，运行时不会通过
降低置信阈值来补偿。登录、删除、支付、发送、安装等关键动作还要求任务明确包含同一
语义且验证置信度至少 0.95；不满足就停止。

## 11. 常见问题

### 面板打不开

- 确认控制台出现 `[dashboard] live at http://127.0.0.1:8787`。
- 任务完成后服务会随运行时关闭，这是正常行为。
- 端口被占用时改用 `--dashboard-port 8788`；正式批量矩阵使用 `0` 禁用面板。

### OCR 未启用

确认安装了 `requirements-vision-lock.txt`，并检查启动日志是否出现
`[perception] RapidOCR enabled`。证据绑定任务没有 OCR 时会失败关闭，不能把
`--ocr off` 当成绕过方式。

### 一直 ABSTAIN 或安全阻断

检查目标是否把“要点击的初始标签”和“目标页才会出现的完成证据”混在一起；再检查
文字是否真的可见、窗口是否被遮挡。先从高清、静止、单页任务开始，不要扩大恢复次数。

### 采集间隔超标

确认 DLL 摘要正确、面板“最新帧源”优先为 `windows_graphics_capture`，关闭会覆盖 MuMu
的窗口和高负载程序。GDI 只是主源超过 250ms 没有新帧时的最高 4Hz 心跳源。

### 点击无效果或重复

查看时间线的预期效果与 `metrics.json`。首次无效只允许同目标重新定位一次；若同一状态
和动作重复三次，或 2–6 步状态环重复两轮且没有语义进展，运行时应进入恢复或阻断，
不应随机点击。

## 12. 正式资格验收

单次成功只是开发冒烟。正式 MuMu 20×5、离线 200 样本、注入循环、30 分钟稳态与
回放流程见 [VLM 闭环验收运行手册](../runbooks/vlm-closed-loop-qualification.md)。只有
所有证据绑定同一个干净 Git revision，且发布台账其余 UGA-075 门禁也完成后，才能
声明最终验收通过。
