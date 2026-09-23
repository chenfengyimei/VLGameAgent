# Universal Game Agent 完整介绍、配置与运行教程

## 1. 项目定位

Universal Game Agent（UGA）是一个面向 Windows 的通用视觉游戏智能体运行时。它把一个普通游戏窗口抽象成“可观察、可决策、可执行、可验证”的计算机使用环境：

1. 从指定窗口持续捕获最新画面。
2. 用 OCR 提取界面文字与位置。
3. 将当前画面、目标、历史和可选局部细节交给视觉语言模型。
4. 要求模型输出结构化、单步、带目标框的动作。
5. 在最新帧上重新检查窗口、焦点、坐标、页面和安全条件。
6. 执行一次鼠标或键盘动作。
7. 观察动作后的画面，验证变化，再决定下一步。

它不是固定坐标连点器，也不依赖游戏内部接口、内存读取、插件注入或特定引擎。新增目标通常只需要一个窗口档案；稳定流程可以进一步沉淀为独立策略，通用运行时本身仍保持与标题无关。

## 2. 核心能力

| 能力 | 说明 |
| --- | --- |
| 窗口捕获 | 支持 Windows Graphics Capture、DXGI Desktop Duplication 与兼容回退 |
| 视觉理解 | OCR、本地或云端视觉语言模型、可选二次验证器 |
| GUI 规划 | 每轮最多一个动作，显式目标标签、目标框、预期效果与风险 |
| 实时控制 | 鼠标、键盘、相对/绝对指针、可扩展手柄契约 |
| 安全边界 | 唯一窗口、焦点、帧新鲜度、禁点区、敏感页、租约、看门狗与急停 |
| 结果验证 | 动作前后像素、OCR、页面状态与任务代数共同判定有效性 |
| 可观测性 | 本机看板显示画面、OCR、决策、动作、效果、延迟和拦截原因 |
| 数据闭环 | Episode 录制、回放、数据处理、质量门禁、训练与评测 |
| 工程交付 | 锁定依赖、严格类型检查、测试、发布清单与证据哈希 |

## 3. 运行架构

```text
窗口发现 ─► 捕获环 ─► 最新帧缓冲 ─► OCR / 页面状态
                                      │
                                      ▼
任务目标 ─────────────────────► VLM / 策略路由
                                      │
                                      ▼
                         结构化单步 Grounded Action
                                      │
                                      ▼
            敏感页门禁 / 禁点区 / 新鲜度 / 焦点 / 控制租约
                                      │
                                      ▼
                            唯一物理输入执行器
                                      │
                                      ▼
                        新画面与动作效果独立验证
```

慢速视觉推理与实时控制分离。模型负责语义决策，不直接获得无限制坐标与输入权限；运行时负责把决策重新锚定到最新画面，并有权拒绝执行。

## 4. 环境要求

普通使用者：

- Windows 10/11 x64。
- Python 3.11 或 3.12 x64。
- 支持图片输入的 OpenAI-compatible 视觉模型接口。
- 一个窗口化或无边框窗口化的目标游戏。

开发者按需安装：

- Node.js 24：修改看板、回放器或数据查看器时使用。
- Rust 与 MSVC Build Tools：编译原生捕获 DLL 时使用。
- CUDA/PyTorch：只在训练可选神经马达策略时使用。

## 5. 获取项目

```powershell
git clone https://github.com/chenfengyimei/VLGameAgent.git
cd VLGameAgent
```

如果仓库已经存在：

```powershell
git status
git pull --ff-only
```

工作区有未提交修改时不要直接覆盖；先让操作者决定提交、暂存或保留。

## 6. 一键安装

```powershell
.\install.cmd
```

安装脚本会：

- 创建项目内 `.venv`。
- 按哈希锁定文件安装运行依赖。
- 安装 OCR 与视觉依赖。
- 以 editable 模式安装当前源码。
- 执行安装后的测试。

安装后运行：

```powershell
.\check.cmd
```

自检不会操作目标游戏。它检查运行时入口、捕获工具入口和 OCR 后端是否可用。

也可以手动安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-vision-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
```

## 7. 创建目标游戏档案

复制通用模板：

```powershell
Copy-Item .\configs\games\generic-visual-game.example.yaml `
  .\configs\games\my-game.yaml
```

最重要的字段：

```yaml
game:
  id: my-game
  display_name: My Game
process:
  executable: [Game.exe]
window:
  preferred_capture: windows_graphics_capture
  title_pattern: '^My Game$'
camera:
  type: absolute_pointer
controls:
  interact: {kind: mouse_button, code: "left", confirmed: true}
```

### 7.1 窗口匹配

`process.executable` 与 `window.title_pattern` 共同限定目标。推荐：

- 使用精确进程文件名。
- 正则以 `^` 开头、`$` 结尾。
- 不要匹配启动器、更新器、管理器或多个同名窗口。
- 分屏、多开时应让标题或 PID 可唯一识别。

### 7.2 指针类型

- GUI、卡牌、回合制或移动端窗口：通常使用 `absolute_pointer`。
- 第一/第三人称镜头：通常使用 `relative_mouse`，并配置灵敏度。
- 点击型界面至少需要确认过的左键 `interact` 绑定。

### 7.3 禁点区域

`no_click_regions` 使用归一化坐标 `[left, top, right, bottom]`，范围 0 到 1。例如：

```yaml
no_click_regions:
  - [0, 0, 1, 0.06]
```

可用于排除系统标题栏、模拟器工具栏、直播悬浮层或账号操作区。宁可先扩大禁点区，也不要在未校准区域冒险点击。

### 7.4 敏感动作

`critical_action_terms` 应覆盖登录、支付、购买、实名、身份证、删除、发送、安装等账号级或不可逆行为。模型看到这些词时不会自动获得执行权限。

### 7.5 校准热点

只有经过人工确认的稳定控件才应写成 `normalized_hotspot`。未确认的返回、关闭或技能按钮应保持注释或删除；让模型基于最新画面定位，比错误的固定热点更安全。

启动器会拒绝任何仍包含 `CHANGE_ME` 的档案。

## 8. 配置视觉模型

UGA 通过 OpenAI-compatible API 调用视觉模型。需要四个值：

- `BaseUrl`：API 根地址。
- `Model`：服务暴露的模型名。
- `ApiKeyEnv`：保存密钥的环境变量名，默认 `UGA_VLM_API_KEY`。
- 供应商特性：是否使用 JSON object 模式、是否关闭 thinking。

### 8.1 云端模型

```powershell
$env:UGA_VLM_API_KEY = "your-key"

.\start.cmd `
  -Profile .\configs\games\my-game.yaml `
  -Goal "打开任务界面" `
  -Model "your-vision-model" `
  -BaseUrl "https://provider.example/v1" `
  -JsonObject
```

密钥只放在环境变量中。不要写入 YAML、启动脚本、截图、日志、Issue 或 Git 提交。

### 8.2 本地模型

```powershell
.\start.cmd `
  -Profile .\configs\games\my-game.yaml `
  -Goal "打开任务界面" `
  -Model "local-vision-model" `
  -BaseUrl "http://127.0.0.1:1234/v1" `
  -NoThinking
```

启动前先确认 `/v1/models` 能看到同名模型，并确认模型支持图片输入与结构化 JSON 输出。

## 9. 编写好目标

目标应描述结果，不要塞入几十步坐标脚本。好的目标包含：

- 当前要完成的单一结果。
- 哪些页面属于敏感边界。
- 何时应该等待而不是重复点击。
- 可见的完成证据。

示例：

```text
打开当前任务面板并推进一个安全步骤；每次动作后等待新画面；
遇到登录、支付、购买或账号页面立即停止。
```

`-GoalEvidence` 可以重复传入。只有新鲜 OCR 画面满足全部证据时，模型的 DONE 才会被接受：

```powershell
-GoalEvidence "任务完成" -GoalEvidence "奖励已领取"
```

不要把容易在背景公告中出现的普通词当作完成证据。

## 10. 第一次运行：有界监督模式

```powershell
.\start.cmd `
  -Profile .\configs\games\my-game.yaml `
  -Goal "安全推进当前可见目标的一步" `
  -GoalEvidence "步骤完成" `
  -Model "your-vision-model" `
  -BaseUrl "https://provider.example/v1" `
  -DurationSeconds 300 `
  -Record .\runs\first-supervised
```

建议第一次只运行 3 到 5 分钟，并全程观察：

1. 看板画面是否来自正确窗口。
2. OCR 是否能读到关键按钮或任务文字。
3. 模型动作框是否覆盖真实控件。
4. 被抑制动作的原因是否合理。
5. 无效果动作是否停止重复。
6. 功能页结束后能否退出。

## 11. 长期运行

短测通过后：

```powershell
.\start.cmd `
  -Profile .\configs\games\my-game.yaml `
  -Goal "持续推进当前可见任务；敏感页停止；无效果时重新观察" `
  -Model "your-vision-model" `
  -BaseUrl "https://provider.example/v1" `
  -DurationSeconds 0 `
  -Continuous `
  -Record .\runs\continuous
```

`DurationSeconds 0` 表示一直运行到 `Ctrl+C` 或紧急停止。`Continuous` 允许一个目标周期结束后继续新周期，但不会绕过已锁存的安全阻塞。

长期运行建议：

- 保持窗口尺寸与 DPI 不变。
- 关闭会遮挡目标窗口的通知和悬浮窗。
- 使用窄窗口正则，避免窗口重建后绑定错误。
- 保留看板和 Episode 证据。
- 先使用 `model-first`；只有已经注册、测试并与目标匹配的规则才使用 `rules-first`。

## 12. 启动脚本参数

```powershell
.\start.cmd --help
```

| 参数 | 含义 |
| --- | --- |
| `-Profile` | 目标 YAML 档案，必填 |
| `-Goal` | 当前运行目标，必填 |
| `-GoalEvidence` | 可重复的 OCR 完成证据 |
| `-Model` | 视觉模型名，必填 |
| `-BaseUrl` | OpenAI-compatible API 地址，必填 |
| `-ApiKeyEnv` | API Key 环境变量名 |
| `-DurationSeconds` | 运行秒数；0 为不限时 |
| `-Continuous` | 完成后开始下一周期 |
| `-PlanningMode` | `model-first` 或 `rules-first` |
| `-CoordinateSpace` | `normalized_1000` 或 `unit` |
| `-NoThinking` | 兼容供应商的无思考输出开关 |
| `-JsonObject` | 使用 JSON object 响应模式 |
| `-DashboardPort` | 本机看板端口；0 关闭 |
| `-Record` | Episode 输出目录 |
| `-Python` | 显式 Python 路径 |
| `-SkipNativeCapture` | 不加载仓库内原生捕获 DLL |

上述值也可通过 `UGA_PROFILE`、`UGA_GOAL`、`UGA_VLM_MODEL`、`UGA_VLM_BASE_URL` 提供。

## 13. 看板与日志

默认看板：

```text
http://127.0.0.1:8787
```

重点字段：

- `status`：运行、阻塞、完成或停机。
- `screen_type` / `feature_page`：当前页面分类。
- `current_action`：当前候选动作。
- `decision source`：模型、OCR 快速规则或恢复逻辑。
- `action_effect`：pending、verified、ineffective。
- `suppressed`：安全层拒绝及原因。
- `frame_age`：决定帧与执行帧的新鲜度。
- `recent_failure` / `last_planner_error`：最近故障。

调试时先保存事实：当前截图、最后 20 条事件、动作来源、效果状态与任务文字。不要仅根据“看起来卡住”修改提示词。

## 14. 停止与急停

- `Ctrl+C`：请求正常停止，允许运行时清理。
- `Ctrl+Shift+F12`：紧急停止并锁存，撤销控制租约、清空队列、释放按键。
- 看门狗超时：自动执行 fail-closed 停机。

急停后应先查明原因，再重新启动；不要自动清除锁存并继续点击。

## 15. 常见问题

### 15.1 一直 WAIT

检查：

- 目标窗口是否可见且前台。
- OCR 是否启用并识别到有效文字。
- 模型接口是否超时或限流。
- 页面是否正在加载、自动寻路或自动战斗。
- 是否存在敏感页、旧帧、歧义目标或未满足的焦点守卫。

WAIT 本身不是错误；它经常代表运行时拒绝在证据不足时冒险点击。

### 15.2 重复点击同一位置

立即急停。检查事件中的 `source` 与 `action_effect`：

- 模型来源：增加页面状态约束或禁止无上下文按钮。
- OCR 快速规则：收紧识别锚点，要求标题、正文与控件共同出现。
- `ineffective`：确保冷却与无效果上限生效。
- 页面已完成：增加完成态识别和确定性退出规则。

### 15.3 进入无关页面后不退出

不要依赖模型“自己想明白”。为该页面选择至少两个稳定且互相独立的 OCR 锚点，添加高优先级返回规则，并用真实截图测试“标题识别”和“标题漏识别”两种情况。

### 15.4 点击偏移

检查 Windows 缩放、窗口客户区、分辨率变化、标题栏高度、坐标尺度与捕获边框。固定热点必须以客户区归一化坐标校准，模型目标必须在最新帧重新锚定。

### 15.5 云端模型不可用

检查：

```powershell
$env:UGA_VLM_API_KEY
Invoke-RestMethod https://provider.example/v1/models `
  -Headers @{ Authorization = "Bearer $env:UGA_VLM_API_KEY" }
```

不要把命令输出中的密钥复制到聊天或 Issue。401 通常是认证错误，429 是限流，5xx 是服务端故障；持续重试应有上限和退避。

### 15.6 捕获黑屏或静止

优先构建并使用原生 WGC/DXGI 后端。兼容回退对某些 GPU 呈现窗口只能获得陈旧画面。运行时会对原生 DLL 做 SHA-256 固定，避免加载未知二进制。

## 16. Episode、回放与数据

带 `-Record` 的运行会生成 Episode。常用命令：

```powershell
uga-replay <episode-directory>
uga-dataset validate <episode-directory> --output quality.json
uga-dataset process <episode-directory> --output samples.jsonl
uga-dataset view <episode-directory>
```

只有带真实动作回执、前置帧、动作后证据和一致身份信息的数据才应进入训练集。录制成功不等于任务成功，回放一致不等于模型泛化通过。

## 17. 开发者验证

仓库内置 `.tooling` 时：

```powershell
$env:PYTHONPATH = "$PWD\.tooling;$PWD"
python -m ruff check .
python -m mypy uga apps
python -m pytest
```

前端：

```powershell
npm ci
npm run typecheck
npm run build
```

Skill 验证：

```powershell
python C:\path\to\skill-creator\scripts\quick_validate.py `
  .\skills\universal-game-agent-setup
```

## 18. 给另一个 Agent 使用

仓库内置 Skill：

```text
skills/universal-game-agent-setup/SKILL.md
```

它指导 Agent 完成仓库获取、安装、档案创建、模型配置、离线自检、有界运行和证据化排错。可直接转发的完整提示词见 [AGENT_HANDOFF_PROMPT.zh-CN.md](AGENT_HANDOFF_PROMPT.zh-CN.md)。

## 19. 安全与责任边界

- 只控制你有权操作的设备、账号和游戏。
- 不要绕过反作弊、访问控制、支付确认、身份验证或平台规则。
- 不要在支付、登录、实名、删除、交易或账号设置页面启用无人值守操作。
- API Key、Cookie、令牌、账号标识和个人截图不得进入 Git。
- 自动化稳定性必须以真实受监督运行和可复核证据为准，不以提示词或单元测试代替。

项目安全策略见 [SECURITY.md](../SECURITY.md)，实现架构见 [ARCHITECTURE.md](../ARCHITECTURE.md)。
