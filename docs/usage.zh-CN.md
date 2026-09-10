# UGA（Universal Game Agent）完整介绍与使用教程

> 本文档面向第一次接触本项目的使用者，从安装到发布打包逐步给出可直接复制的命令。
> 英文总览见 [README.md](../README.md)，架构细节见 [ARCHITECTURE.md](../ARCHITECTURE.md)。

---

## 1. 这是什么项目

UGA 是一个 **Windows 优先的通用游戏 computer-use 智能体运行时**：它观察屏幕像素、理解目标、
选择技能，并通过键盘 / 鼠标 / 手柄输入操作系统与游戏交互。所有输入都经过显式的安全边界
（租约、仲裁器、看门狗、紧急停止热键）。

项目包含一条完整的"观察 → 决策 → 控制 → 记录 → 数据 → 训练 → 评测"链路：

| 模块 | 作用 |
|---|---|
| 捕获层 | 三种后端抓取目标窗口画面：**WGC**（Windows Graphics Capture，主选）、**DXGI** 桌面复制、**GDI** 兼容回退 |
| 控制层 | 语义/规范/物理三层动作契约 + 30Hz 调度器 + **租约仲裁**（唯一授权方） |
| 安全层 | 焦点守卫、控制租约过期、**看门狗**、`Ctrl+Shift+F12` 紧急停止 |
| 记录层 | Episode 事务式录制（Parquet 动作 + H.264 视频 + 校验和），**确定性回放** |
| 数据层 | Episode 质量门禁、防泄漏切分、数据集清单（含许可证元数据） |
| 训练层 | 确定性 BC 马达策略训练（`uga-train`），Fast Policy 行动块 |
| 评测层 | UGA-Bench（捕获/推理/规划/输入延迟 + 任务成功率） |
| 界面 | 离线仪表盘、回放调试器、数据集查看器 |
| 资格认证 | `uga-qualify` 证据台账：门禁 + SHA-256 哈希锚定的证据链 |

**安全边界**：本项目仅用于单机、离线、私有测试、自有、开源或研究沙箱环境。
竞技多人游戏自动化、反作弊对抗、无人值守在线对局一律超出范围。

---

## 2. 环境要求

- Windows 10/11
- Python **3.11+**（运行时依赖：av、pyarrow、PyYAML）
- 仅当修改前端（Dashboard/Replay/Viewer）时才需要 Node.js 24（用户无需，已编译资产随包分发）
- Rust 工具链（仅修改原生捕获 DLL 时需要）

---

## 3. 安装

### 方式 A：源码安装（开发机）

```powershell
git clone https://github.com/chenfengyimei/VLGameAgent.git
cd VLGameAgent
python -m pip install -e .
```

### 方式 B：发布包（见第 8 节打包流程）

解包后先装 wheel，再用包内 `run_uga.ps1` 启动（它会校验清单并自动加载原生捕获 DLL）。

---

## 4. 快速冒烟（确认一切正常）

```powershell
uga-agent                      # 运行时生命周期冒烟
uga-example-game --headless-smoke   # 测试世界无头自检，应输出 "success": true
python -m pytest               # 全量测试（应全绿）
```

---

## 5. 分步教程

### 5.1 启动测试游戏窗口

`uga-example-game` 打开一个开发者自有的测试世界（Tk 窗口），共 4 个确定性场景：

```powershell
uga-example-game                              # 场景 1：exploration（默认）
uga-example-game --scenario realtime_control # 场景 2：实时控制（玩家在右、目标在左）
uga-example-game --scenario gui_navigation   # 场景 3：GUI 菜单导航
uga-example-game --scenario heldout_diagonal # 场景 4：锁定对角测试（用作泛化 Test D）
```

操作方式：WASD 移动、鼠标转向、E 交互、Esc 菜单、R 重置。

> **纪律**：跑资格认证时请保持该窗口**完全可见**（无任何窗口遮挡、最好放到副屏），
> 并让它与认证命令使用**相同的 `--scenario`**。

### 5.2 捕获探针

对指定标题的窗口抓帧并输出延迟/帧率/回退诊断报告：

```powershell
uga-capture-probe --title '^UGA Fixture World$' --frames 120 `
  --output runs/capture-smoke.json
```

`--backend` 可选 `auto`（默认，按 WGC→DXGI→GDI 择优）、`windows_graphics_capture`、
`dxgi_duplication`、`gdi_fallback`；`--duration-seconds` 可代替 `--frames` 做浸泡。

**启用 WGC / DXGI 需先固定原生 DLL（安全策略：必须显式哈希锚定才会加载）**：

```powershell
$env:UGA_NATIVE_CAPTURE_DLL  = "native\target\release\uga_capture.dll"
$env:UGA_NATIVE_CAPTURE_SHA256 = (Get-FileHash $env:UGA_NATIVE_CAPTURE_DLL).Hash.ToLower()
```

> 已知特性：GDI 回退后端对 GPU 呈现的窗口（D3D/Vulkan）只能看到**陈旧快照**，
> 属兼容层固有限制；实时内容请用 WGC 或 DXGI。

### 5.3 资格认证（捕获 + 控制 + 录制 + 回放一体）

先启动一个已知 PID 的测试世界窗口（保持可见），然后：

```powershell
uga-qualify fixture `
  --title '^UGA Fixture World [realtime_control]$' `
  --expected-pid <上面窗口的进程ID> `
  --scenario realtime_control `
  --duration-seconds 30 `
  --backend auto `
  --episode-root runs/qualification `
  --output runs/qualification/report.json `
  --allow-physical-input `
  --exercise-watchdog-timeout `
  --exercise-emergency-hotkey
```

报告里逐项记录：捕获诊断、动作执行率、录制质量、回放校验、
看门狗/紧急热键演练结果。**运行期间请勿操作机器**（焦点被抢占会导致失败——
资格认证对遮挡焦点问题会直接响亮中止并提示原因）。

### 5.4 录制与回放

每次资格认证/语料运行都会产出 Episode（动作 Parquet + 视频 + 校验和）。直接检查与回放：

```powershell
uga-replay <episode目录>                 # 校验回放
uga-dataset validate <episode目录> --output quality.json
uga-dataset process <episode目录> --output samples.jsonl   # 时间对齐样本
uga-dataset view <episode目录>           # 数据集查看器
```

### 5.5 训练（确定性马达策略）

```powershell
uga-train prepare --episode <episode目录> --output samples.jsonl     # 可多个 --episode
uga-train motor `
  --samples samples.jsonl `
  --dataset-manifest data/datasets/v1/manifest.json `
  --dataset-root data/datasets/v1 `
  --config configs/training/motor_bc.yaml `
  --output runs/models/fixture-motor-v1 `
  --policy-version fixture-motor-v1 `
  --source-revision <git提交哈希> `
  --base-model-license <基础模型许可证>
uga-train verify runs/models/fixture-motor-v1/training-artifact.json
```

### 5.6 基准评测

```powershell
uga-benchmark validate-config configs/benchmarks/uga-bench-fixture.yaml
uga-benchmark fixture `
  --config configs/benchmarks/uga-bench-fixture.yaml `
  --artifact runs/models/fixture-motor-v1/training-artifact.json `
  --output runs/bench-runs.jsonl `
  --report runs/bench-report.json
uga-benchmark summarize runs/bench-runs.jsonl --config configs/benchmarks/uga-bench-fixture.yaml
```

### 5.7 仪表盘

```powershell
uga-dashboard --output dashboard.html                        # 离线生成
uga-dashboard --serve --host 127.0.0.1 --port 8765           # 本地服务（仅回环）
```

浏览器打开 `http://127.0.0.1:8765`（命令需带 CSRF 令牌，服务启动时打印）。

### 5.8 紧急停止

任何时刻按 **`Ctrl+Shift+F12`**：立即吊销全部控制租约、清空调度队列、
释放所有按住的键 —— 看门狗在心跳停滞 5 秒时也会自动触发同样的 fail-closed 停机。

### 5.9 实战：MuMu 模拟器（安卓游戏）

安卓模拟器的窗口就是一个普通 Win32 目标：UGA 用捕获后端看它的画面，
用鼠标点击（经模拟器转成安卓点按）操作其中的游戏。仓库自带「仙遇（MuMu）」的
档案文件 `configs/games/mumu-xianyu.yaml`，关键字段：

```yaml
window:
  preferred_capture: windows_graphics_capture   # 首选 WGC
  title_pattern: '^MuMu安卓设备(-\d+)?$'         # 只匹配游戏窗口，不匹配管理器
controls:
  interact: {kind: mouse_button, code: "left", confirmed: true}  # 点击 = 左键脉冲
camera:
  type: absolute_pointer                        # 规范动作携带物理屏幕坐标
safety:
  environment_class: developer_owned            # 仅限开发者自有环境
```

运行端到端智能体循环（发现窗口 → 激活前台 → 捕获 → 观测 → 策略 → 调度注入 → 可选录像）：

```powershell
$env:UGA_NATIVE_CAPTURE_DLL  = "native\target\release\uga_capture.dll"
$env:UGA_NATIVE_CAPTURE_SHA256 = (Get-FileHash $env:UGA_NATIVE_CAPTURE_DLL).Hash.ToLower()

uga-agent run --profile configs/games/mumu-xianyu.yaml `
  --goal "Interact with the target" `
  --duration-seconds 60 `
  --record runs/episodes
```

说明：

- **窗口发现**：标题必须恰好匹配一个窗口（游戏窗 `MuMu安卓设备-1`，
  而非管理器窗 `MuMu模拟器`），且能成功取得前台，否则直接报错退出。
- **捕获**：固定原生 DLL 前会自动降级 GDI 兼容回退（MuMu 的 GDI 表面会持续更新，
  但按档案偏好仍应使用 WGC）。
- **点击**：`absolute_pointer` 相机 + `mouse_button` 绑定的组合下，规范动作携带
  物理屏幕坐标，环境层产出「绝对移动 → 按下 → 抬起」三段物理动作；
  点击点默认为客户区的 50%/79% 处（「开启仙途」按钮），可用 `--tap-x-fraction`
  / `--tap-y-fraction`（客户区比例坐标，0~1）覆盖到当前界面的任意按钮；
  `--tap-delay`（默认 2 秒）指定注入时机。
- **产出**：结束后打印 `episode: runs/episodes/<episode-id>`，内含 H.264 视频、
  动作 Parquet、事件日志与校验和，可直接用 `uga-replay` / `uga-dataset` 检查；
  录制器会自动把 WGC 带边框的奇数尺寸裁剪到偶数再编码。
- **纪律**：与其他认证运行一致——运行期间请勿操作机器（抢焦点会让点击落点失效）；
  紧急停止热键 `Ctrl+Shift+F12` 随时可用。

---

## 6. 资格认证台账（可选的正式证据链）

`uga-qualify` 维护一个带哈希的证据台账，覆盖：自动化测试、构建安装、捕获浸泡、
控制硬件、10 分钟录制、5 小时数据集、模型训练、四场景泛化基准、许可证治理。

```powershell
uga-qualify init runs/qualification-v1 --source-revision <commit>
uga-qualify record runs/qualification-v1 capture-soak passed `
  --evidence "30分钟矩阵通过" --artifact capture/soak-report.json
uga-qualify status runs/qualification-v1
uga-qualify preflight runs/qualification-v1 --project-root . [...]
```

V1 的泛化主张口径为**自有 Fixture 四场景**（Train A/B/C + 锁定 heldout_diagonal）；
真实商业游戏数据列入 V2（需授权与权利记录）。

---

## 7. 开发与测试门禁

```powershell
.\.tooling\bin\ruff.exe check .
python -m mypy uga apps          # 严格模式
python -m pytest -q
npm ci; npm run typecheck; npm run build   # 仅前端改动需要
Push-Location native; cargo fmt --all --check; cargo clippy --workspace --all-targets --locked -- -D warnings; cargo test --workspace --locked; Pop-Location
```

本仓库不含任何 copyleft 依赖（全部 MIT/Apache-2.0/BSD-3-Clause），源码以 **MIT** 授权。

---

## 8. 打包发布

```powershell
npm ci --include=dev        # 仅首次或 package-lock 变更后
powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1
```

产物：`dist/` 下的 wheel + 源码包，以及完整发布目录（含 `native\uga_capture.dll`、
`run_uga.ps1` 启动器、`release-manifest.json` 清单、LICENSE、文档与配置）。

使用发布包：

```powershell
python -m pip install <bundle里的wheel>
.\run_uga.ps1 -ManifestSha256 <清单的SHA-256>   # 带外锚点：先验清单再验一切
```

`run_uga.ps1` 逐层校验：带外清单摘要 → 全包哈希树 → 原生 DLL 摘要，
任何一项不符都会拒绝启动。`release-manifest.json` 的门禁状态如实反映证据完成度，
未完成全部门禁前它不会声称 `releasable: true`。

---

## 9. 实战注意事项（踩坑记录）

1. **窗口遮挡**：测试窗口被任何窗口盖住 → 点击落到覆盖窗口上、捕获退化为陈旧帧；
   资格认证会直接中止并提示"obstructed"。把窗口放到无遮挡的副屏最稳。
2. **场景匹配**：`uga-example-game --scenario X` 与 `uga-qualify fixture --scenario X`
   必须一致，否则脚本化移动方向相反、永远不成功。
3. **运行期间勿动机器**：抢焦点会让认证的时序窗失效。
4. **npm**：本机 `~/.npmrc` 设了 `omit=dev`，装依赖务必 `npm ci --include=dev`。
5. **原生 DLL**：不设 `UGA_NATIVE_CAPTURE_DLL`+`UGA_NATIVE_CAPTURE_SHA256` 时
   WGC/DXGI 报 "not installed"，这是刻意的安全设计。
6. **PowerShell 引号**：`Start-Process -ArgumentList` 不给含空格参数加引号，
   传复杂参数请用 `&` 调用或脚本文件。

---

## 10. 命令速查

| 命令 | 用途 |
|---|---|
| `uga-agent` | 运行时生命周期冒烟 |
| `uga-agent run --profile <yaml>` | 对真实窗口运行完整智能体循环（如 MuMu 模拟器，见 5.9） |
| `uga-example-game` | 启动测试世界（4 场景 / `--headless-smoke` / `--focus-sink`） |
| `uga-capture-probe` | 窗口捕获诊断报告 |
| `uga-qualify fixture\|corpus\|...` | 资格认证与证据台账 |
| `uga-replay <episode>` | 回放校验 |
| `uga-dataset validate\|process\|view` | Episode 质检 / 样本对齐 / 查看器 |
| `uga-train prepare\|motor\|verify` | 训练 |
| `uga-benchmark fixture\|summarize\|...` | 基准评测 |
| `uga-dashboard` | 仪表盘（离线 / 本地服务） |
| `uga-release-manifest <bundle> [--verify-existing]` | 生成/校验发布清单 |
| `uga-dependency-inventory` | 依赖许可清单 |

许可证：**MIT**（见 [LICENSE](../LICENSE)）。
