# VLGameAgent 仙遇/MuMu 自动推进项目交接（给 GLM-5.3-Flash）

日期：2026-09-15  
工作区：`D:\xm\VLGameAgent`  
当前状态：已软停止；`127.0.0.1:8787` 无监听；不要在完成 P0 修复前重新长跑。  
代码状态：所有本轮修改均保留在未提交工作区，禁止 reset、checkout 或覆盖用户已有修改。

## 1. 你要完成的最终目标

把本项目做成一个可长期运行的 MuMu 游戏 GUI 闭环代理：

1. 以低资源占用持续捕获 MuMu 的最新画面。
2. 每次云端视觉模型请求只发送一张最新截图。
3. OCR 提供文字与精确坐标，视觉模型负责理解当前画面与决定下一步。
4. 每次只执行一个可验证的低风险 GUI 动作。
5. 动作必须真实发送给 MuMu，并在新画面中验证效果；“模型输出 click”不能冒充“已经物理点击”。
6. 持续推进当前主线任务、对话、引导、养成和功能页流程。
7. 普通卡住时要在同一进程内恢复，不得周期性重启整个项目。
8. 除非用户明确停止，否则长期运行。

## 2. 用户已经明确的产品规则（不得改写）

- “9秒后自动继续”表示对话最晚自动继续时间，不是让代理等待 9 秒。看到它要立即推进对话。
- 普通对话通常会显示“回顾剧情”。看到“回顾剧情”或“X秒后自动继续”时，立即按 Space 或点击对白继续区；优先使用已验证有效的方式。
- 左上角主线任务文字是当前目标来源。任务文字变化时要记录为最新任务，并让后续决策围绕它执行。
- OCR 把“桃夭”识别为“桃天”等单字波动，不代表任务变化；必须使用模糊匹配和位置连续性归并。
- 功能操作完成后要退出功能页，重新查看世界界面和最新任务。
- 本游戏里 Esc 不能当作可靠返回；必须点击游戏画面左上角的返回控件。
- 不需要高频采集。当前目标为捕获 2 FPS、观察 1 Hz、视觉模型最短决策间隔 3 秒。
- 云端模型每次只收一张最新图，不能发送多帧历史图或额外裁剪图。
- 不要把 API key 写进仓库、命令日志、仪表盘或交接文档。

## 3. 当前已实现内容

### 3.1 云端 GLM-4.6V

- 默认模型：`glm-4.6v`
- API 基址：`https://open.bigmodel.cn/api/paas/v4`
- 使用兼容 OpenAI 的视觉请求。
- 云端使用 JSON object 输出模式。
- 支持 GLM 返回的 0–1000 坐标归一化。
- 支持顶层 `answer` 中再次包裹 JSON 字符串的兼容解析。
- 最大输出 256 token，关闭 thinking，超时 60 秒。
- API key 从用户环境变量 `UGA_VLM_API_KEY` 读取；仓库中没有明文 key。

### 3.2 资源限制

- 捕获频率：2 FPS。
- 观察频率：1 Hz。
- 视觉决策间隔：3 秒。
- 每次 VLM 请求：1 张最新截图。
- 时间帧数：1。
- 目标裁剪数：0。
- 当前上传宽度：448 px。

### 3.3 OCR 快速路径

已经加入：

- 必须先在左上角看到真实“主线”标题，才把附近文字当作任务追踪，避免误点其它页面的左侧文字。
- 对“桃夭/桃天”这类同位置、长度至少 4 的目标，使用 `SequenceMatcher >= 0.72` 容忍单字 OCR 波动。
- “传功/疗伤/按住/长按”会生成长按动作，当前长按时间 700 ms。
- “9秒后自动继续”或“回顾剧情”会生成 `dialogue_advance`，配置为 Space（虚拟键码 32），不再返回 WAIT。
- `提交/跳过/继续/下一步/确定/领取/传功/疗伤` 等明确动作可走 OCR 快速路径。
- “上阵”必须精确匹配，不能把“未上阵”误当成“上阵”。
- 长篇“恭喜某玩家领取……”跑马灯已通过长度限制排除，不能再被当成“领取”按钮。
- 已移除“仅凭屏幕底部两行文字猜测对话”的宽泛启发式；没有明确对话提示时交给单图视觉模型。

### 3.4 模型语义与 OCR 坐标结合

已加入 `model_ocr_snap`：

- 模型决定“点哪个文字”。
- 如果最新 OCR 中存在精确、包含或高相似度的同名文字，就把模型的宽松/偏移 bbox 吸附到最新 OCR bbox。
- 监督器的 OCR 重叠判断改为双向覆盖率，允许“模型框包住较小 OCR 文字框”。
- 这解决了模型正确识别“获取灵宠”，但坐标框偏到右侧而被丢弃或点偏的问题。

### 3.5 连续运行和仪表盘

- `--continuous` 模式会保持一个 Python 进程，闭环终止后在进程内重建闭环。
- 仪表盘：`http://127.0.0.1:8787/`
- 仪表盘已显示捕获帧数、最新帧年龄、模型输入帧年龄、逻辑动作数、物理动作数、监督结果、失败数和连续周期数。
- 启动脚本：`scripts\run_mumu_autoplay.ps1`
- 脚本对云端模型不再启动 LM Studio。

## 4. 已验证的现场证据

下面是本轮真实运行中出现过的有效推进，不是单元测试模拟：

- `16:21:12 click(提交)`，OCR 快速路径，0 张模型图片。
- 随后真实推进了 `突破瓶颈`、`确定`、`去完成`。
- `16:31:15 click(铃唤一次)`，云端模型，1 张图。
- 后续从召唤页推进到了灵宠选择/布阵页面。
- 启用 OCR 坐标吸附后，事件出现 `source=model_ocr_snap`，模型语义与 OCR 坐标开始合并。

测试状态：

- 最近一次相关测试：`182 passed`。
- 覆盖的是下方列出的 8 个相关测试文件，不是宣称整个仓库全量测试已通过。
- `compileall` 通过。
- `git diff --check` 通过；仅有已有 CRLF/LF 提示。

## 5. 当前没有解决的 P0 问题

### P0-1：返回动作仍然错误

用户已明确：Esc 不是本游戏可靠的返回方式，必须点击画面左上角返回控件。

当前错误状态：

- `configs/games/mumu-xianyu.yaml` 仍把 `back` 配置为虚拟键 27。
- `ClosedLoopSupervisor` 的安全恢复仍通过 `to_recovery_gui_action()` 发送 Esc。
- 灵宠完成页启发式当前尝试点击 OCR “灵宠”标题并向左偏移 `-0.07`，现场没有稳定退出。
- 因为返回无效果，闭环会耗尽恢复预算并在同一进程内创建新 cycle；用户看到的现象像“项目过一会重新启动”。

必须改成：

1. 为仙遇定义一个视觉返回控件 `ui_back`，类型为 click，不是 virtual key。
2. 用真实截图标注左上角返回控件的可点击中心和区域，不要拿“灵宠”标题文字猜偏移。
3. 提交动作前记录最终归一化坐标与物理屏幕坐标。
4. 点击后验证功能页标题消失、世界 HUD/主线追踪出现，或页面语义签名发生稳定变化。
5. 若一次未生效，允许基于最新帧重定位后再点击一次；之后停止重复并报告具体失败，不得无限 cycle。

### P0-2：没有持久的“当前主线任务状态”

当前 planner 主要是单帧无状态决策。OCR 虽能点左上角任务，但没有可靠维护：

```text
当前主线任务是什么
何时从旧任务更新为新任务
最近一次点任务是否执行
点后是否开始自动寻路/进入新页面
当前功能页是否服务于这条任务
```

这导致模型看得懂图中文字，却不知道跨页面的长期意图。它会在“未上阵/主战位/下阵/获取灵宠”等局部控件之间打转。

### P0-3：提议、提交、物理执行、效果验证没有统一 action trace

当前 decision journal 会记录模型提出的 `click(...)`，但这不保证动作最终通过监督、进入调度器、被物理执行并产生效果。必须给每个动作一个稳定 `action_id`，串起：

```text
model_proposed
-> ocr_snapped
-> supervisor_accepted/rejected
-> scheduler_submitted
-> physical_pointer/key_down/key_up
-> effect_verified/ineffective
-> recovery
```

仪表盘默认应突出“最后一次真实物理动作”，模型提议只能作为辅助信息。

### P0-4：效果判断被动态 OCR 噪声误导

功能页数字、跑马灯或 OCR 抖动会让 `_meaningful_text_change()` 误以为操作生效，导致相同页面继续规划，而不是触发退出。

效果验证必须优先使用稳定页面锚点和目标控件状态：

- 页面标题是否变化。
- 目标按钮是否消失/变灰/文字变化。
- 世界 HUD 与主线追踪是否重新出现。
- 角色是否进入自动寻路状态。
- 对话文本页是否切换。

不要把任意 OCR token 集合变化视为成功。

### P0-5：正常卡住不应造成闭环 cycle 重建

当前正常页面卡住会消耗最多 2 次 recovery，随后 closed loop 进入 BLOCKED；`--continuous` 再创建新闭环并等待约 15 秒。进程没有真的重启，但用户体验等同于周期性重启、状态丢失。

应把“功能页返回、重新观察、重新点击主线”设计成普通状态迁移。只有不可恢复的窗口丢失、输入权限错误或后端长期失败才允许终止/重建。

## 6. 必须新增的任务记忆结构

建议新增 `GameSessionState`（名称可调整），至少包含：

```python
latest_main_task: {
    raw_text: str,
    canonical_text: str,
    bbox: NormalizedBox,
    confidence: float,
    first_seen_frame_id: str,
    last_seen_frame_id: str,
    observed_at_ns: int,
    generation: int,
}
screen_type: str  # world/dialogue/loading/feature/popup/login/unknown
feature_page: str | None
dialogue_active: bool
auto_navigation_active: bool | None
recent_actions: deque[ActionTrace]  # 建议 8–16 条
last_verified_progress_at_ns: int
```

任务更新规则：

1. 仅当“主线”标题在左上任务追踪区域出现时采集任务候选。
2. 使用位置连续性、文本相似度和置信度合并连续帧。
3. `桃夭/桃天` 等高相似文本归为同一个 canonical task，不增加 generation。
4. 新文本连续出现 2 帧，或单帧高置信且旧任务已消失，才更新任务 generation。
5. 每次 VLM 请求明确注入：当前主线、当前页面、最近 3–5 个真实动作及其效果。
6. 功能页退出后必须重新观察主线；不得沿用退出前的局部按钮决定。

## 7. 目标决策优先级

实现为清晰状态机，不要继续堆互相覆盖的字符串启发式：

1. **窗口/捕获不可用**：恢复捕获，不发输入。
2. **加载中**：`WAIT(animation)`，短等待后用最新帧复查。
3. **普通对话**：看到“回顾剧情”或倒计时，立即 Space；200–500 ms 后复查。
4. **对话选项**：点击明确选项，不用 Space 随机选择。
5. **明确弹窗**：确定、领取、关闭；动作后必须验证弹窗消失。
6. **功能页任务步骤**：只执行与 `latest_main_task` 对应的动作。
7. **功能页已完成/同页无进展**：点击视觉 `ui_back`，重新观察世界页。
8. **世界页有主线追踪**：点击最新主线任务文字，触发自动寻路。
9. **世界页任务暂不可见**：只此时允许打开任务总入口。
10. **确实无安全动作**：WAIT；相同状态连续两次后重新定位主线或点击视觉返回，不要重启进程。

## 8. 对话策略的具体要求

- OCR 命中 `回顾剧情` 或正则 `\d+秒后自动继续`：立即构造 `KEY(dialogue_advance)`。
- `dialogue_advance` 绑定 Space（32），与返回控件完全分离。
- 对话推进应使用更短 effect window（建议 250–800 ms），不能沿用功能按钮 3 秒节奏。
- 若 Space 连续两次没有使对白文字或对话页码变化，改为点击对白继续热区一次。
- 出现两个或以上右侧选项时，不使用 Space；让模型基于当前主线和剧情语义选择，再由 OCR 吸附坐标。
- 对话倒计时永远不能映射为 WAIT。

## 9. 视觉返回控件的实现建议

先用当前真实截图建立测试 fixture，再实现，不要凭想象硬编码：

1. 从 MuMu 捕获图中标注左上角返回图标 bbox 和点击中心。
2. 首选图标模板/局部视觉检测；页面标题 OCR 只能用于确认“这是功能页”，不能作为返回按钮坐标。
3. 配置可以设计为：

```yaml
controls:
  ui_back:
    kind: normalized_hotspot
    x: <用真实截图校准>
    y: <用真实截图校准>
    confirmed: true
```

4. `RecoveryDirective.BACK` 应解析成这个鼠标 click，而不是虚拟键。
5. 在执行前保存调试叠加图：模型框、OCR 框、最终框、点击点。
6. 多窗口/DPI/窗口移动后必须通过 `CoordinateTransform` 转成当前物理屏幕坐标，不能保存绝对屏幕坐标。

## 10. 分阶段开发计划与验收标准

### 阶段 A：动作可观测性（先做）

改造 dashboard/journal/event：

- 每个动作生成 `action_id`。
- 记录 source frame id/age、模型 bbox、OCR bbox、最终 bbox、归一化点、物理点。
- 记录 supervisor 结果、scheduler 是否接受、实际输入原语、effect 结果。
- UI 分三列：模型提议 / 最终动作 / 效果验证。

验收：任意一条 `click` 都能回答“最后点了屏幕哪里、是否真的 down/up、为什么判定成功或失败”。

### 阶段 B：视觉返回（P0）

- 删除仙遇恢复路径对 Esc 的依赖。
- 用真实帧实现左上返回控件。
- 删除或替换 `_completed_pet_panel_close_candidate()` 中“点标题并偏移”的脆弱逻辑。
- 同一非进度控件真实执行两次且无稳定效果时，点击视觉返回一次。

验收：从境界、灵宠、召唤、布阵至少 4 个功能页各退出 10 次，成功率 100%，无 Esc。

### 阶段 C：主线任务记忆

- 新增 `GameSessionState`。
- 实现任务候选、模糊归并、generation、过期规则。
- dashboard 展示 raw/canonical 最新主线与更新时间。
- prompt 注入最新主线和真实动作结果。

验收：录制的“桃夭/桃天”动态帧不会导致 generation 变化；任务真正更新时 2 帧内切换。

### 阶段 D：场景状态机

- 明确定义 world/dialogue/dialogue_choice/loading/popup/feature。
- 用场景状态决定可用动作集合。
- 功能页动作必须与当前主线相关。
- 完成或卡住后视觉返回，再点最新主线。

验收：不会在灵宠页循环点击“未上阵/主战位/下阵/获取灵宠”；最多 2 个无效动作后离开页面。

### 阶段 E：效果验证与循环恢复

- 页面锚点优先于 OCR token 集合差异。
- 为 dialogue、popup、task click、feature action、back 分别设 effect contract。
- 普通恢复不消耗终止预算，不创建新 continuous cycle。
- 只有后端/窗口/权限故障才重建闭环。

验收：15 分钟现场运行 `continuous_cycle_count == 1`；同一无效动作不超过 2 次；无周期性 15 秒停顿。

### 阶段 F：资源与长期稳定性

- 保持 2 FPS 捕获、1 Hz 观察、单图请求。
- 对坐标精度做 448 vs 640 单图 A/B；若继续使用 OCR 吸附，优先保留 448。
- 限制最近动作历史为小型结构化摘要，不能把大批截图或日志发给小模型。
- 处理网络超时、429、5xx，使用同进程指数退避；不得重启 MuMu。

验收：连续 60 分钟运行；无内存持续增长；无多图请求；无模型错误导致进程退出。

## 11. 必须补充的测试

使用本轮真实画面建立固定 fixture：

- 境界页：`1/1 + 提交 + 今日境界奖励`，必须点提交。
- 灵宠属性页：数字 `770/15` 不能被当作对话选项。
- 召唤页：长篇首充跑马灯不能触发领取。
- 布阵页：`未上阵` 不能触发“上阵”；精确“上阵”才能触发。
- 模型框偏移、OCR 框正确：最终点 OCR 框中心。
- 对话页：倒计时与“回顾剧情”立即 Space，图片数为 0。
- 对话选择页：选择选项，不按 Space。
- 左上返回：最终物理点落在真实返回图标 bbox 内。
- 同页无效果：两次后视觉返回，不发 Esc，不创建新 cycle。
- 任务 OCR 波动：桃夭/桃天保持同 generation。

现场验收必须另外记录：

- 15 分钟短跑。
- 60 分钟长跑。
- 模型请求图片数始终为 1。
- 捕获间隔 p95 约 500 ms。
- 最新模型输入帧年龄通常小于 2 秒。
- 提议数、监督接受数、物理执行数和效果成功数分别统计。

## 12. 当前相关文件

主要实现：

- `apps/agent/__main__.py`
- `apps/agent/run.py`
- `apps/agent/dashboard.py`
- `uga/core/agent_loop.py`
- `uga/agent/closed_loop.py`
- `uga/policy/grounded_vlm.py`
- `uga/policy/vlm_planner.py`
- `uga/capture/hub.py`
- `uga/gui/control_bridge.py`
- `uga/gui/schema.py`
- `uga/perception/schema.py`
- `configs/games/mumu-xianyu.yaml`
- `scripts/run_mumu_autoplay.ps1`

相关测试：

- `tests/unit/test_grounded_vlm.py`
- `tests/unit/test_closed_loop_supervisor.py`
- `tests/unit/test_vlm_planner.py`
- `tests/unit/test_capture_hub.py`
- `tests/unit/test_agent_dashboard.py`
- `tests/integration/test_agent_loop.py`
- `tests/integration/test_baseline_agent.py`
- `tests/unit/test_live_agent_composition.py`

现场文件：

- `runs/live-agent/dashboard-frame.png`：最后抓取的 MuMu 画面。
- `runs/live-agent/supervisor.log`：多次受控重启和运行日志。

## 13. 验证命令

PowerShell：

```powershell
$env:PYTHONPATH=(Resolve-Path .\.tooling).Path
$python='C:\Users\cy\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $python -m pytest `
  tests\unit\test_grounded_vlm.py `
  tests\unit\test_vlm_planner.py `
  tests\unit\test_capture_hub.py `
  tests\unit\test_agent_dashboard.py `
  tests\unit\test_closed_loop_supervisor.py `
  tests\integration\test_agent_loop.py `
  tests\unit\test_live_agent_composition.py `
  tests\integration\test_baseline_agent.py -q
& $python -m compileall -q apps uga
git diff --check
```

确认软停止：

```powershell
Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue
```

修复 P0 并通过测试后，才允许启动：

```powershell
& .\scripts\run_mumu_autoplay.ps1
```

## 14. 密钥与安全要求

- 不要读取或打印 `UGA_VLM_API_KEY` 的值。
- 只检查变量是否存在，不打印内容或长度以外的信息。
- 不得把 key 写入 YAML、PowerShell 脚本、测试 fixture、日志或提交。
- 用户曾在对话里明文发送过 key，完成开发后应提醒用户轮换；不要在任何回复中复述旧 key。

## 15. 给接手模型的直接执行指令

请从 P0 开始，不要直接重新启动长跑：

1. 先阅读本文件和当前 git diff，保护所有未提交修改。
2. 用 `runs/live-agent/dashboard-frame.png` 与新增真实截图校准左上返回按钮。
3. 把返回从 Esc 改成可验证的鼠标点击，并补测试。
4. 建立 `GameSessionState`，持久记录最新主线任务、页面类型和真实动作效果。
5. 改造 action trace，让 dashboard 区分提议、最终坐标、物理执行和效果。
6. 将决策重构为场景状态机，清除宽泛字符串匹配。
7. 跑相关测试与 15 分钟现场 smoke test；只有 P0 验收通过后再进行 60 分钟长跑。
8. 最终必须留下一个持续运行的单进程实例，并用数据证明：单图、2 FPS、真实 GUI 输入、任务更新、对话即时推进、功能页可返回、无周期性重启。

