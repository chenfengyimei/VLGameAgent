# VLGameAgent 全链路缺陷核查与 GLM‑5.3‑Flash 开发执行计划

**审计日期：2026-09-16**
**源码基线：`34eba5ebe141862d590462c4551dbe0a2514d51a`（main；落盘时已确认等于仓库 HEAD，工作区干净）**
**交付性质：可执行开发计划与缺陷台账，不是已完成的代码修复或正式发布证明。**

配套文件：

- `docs/reviews/audit-coverage-2026-09-16.md` —— 审查覆盖台账与证据边界
- `docs/reviews/bug-ledger-2026-09-16.md` —— 23 条缺陷/风险/资格缺口台账（带状态跟踪）

## 阅读导航

1. 结论与证据边界
2. 项目全链路与现有能力
3. 缺陷、风险和资格缺口台账（23条）
4. 目标、不变量和交付层次
5. 目标接口与执行流程
6. 里程碑、依赖和合并顺序
7. 文件级开发任务卡（D00—D19）
8. 回归测试与故障注入矩阵
9. 数据、训练、发布的独立完成条件
10. GLM执行协议、逐PR完成定义与禁止事项
11. 建议验证命令与交付目录
12. 审查覆盖清单与剩余验证边界
- 附录A：首批范围、关键落地约束与验收目标

---

## 1. 结论与证据边界

### 1.1 首先修安全与证据，不是继续堆叠点击规则

当前最优先的工作是：**锁存停止控制权 → 统一所有动作安全门 → 真实执行回执 → 有界效果/恢复/目标确认 → 可靠模型协议 → 可追踪数据与正式资格。**

现有项目已有可复用的租约、仲裁、输入执行器、FocusGuard、SafetyShutdown、Watchdog、Episode与数据集契约。正确方向是把真实入口接回这些边界，修复跨模块不一致；不是把项目整体推倒，也不是增加“ui_* 一律放行”来改善演示成功率。[S01] [S02] [S03] [S05] [S06]

### 1.2 本次审计实际做了什么

通过 GitHub 连接读取固定提交的架构文档、主循环、闭环监督器、视觉调用、会话状态、采集、输入安全、记录、数据集、训练基线、看板、启动脚本、部分 Native 实现与测试，并读取当前 Actions 结果和 Python 作业日志。执行了 8 项**从所读源码方法/分支提取或重放、使用模拟依赖的离线隔离检查**。

**未声称完成的事：** 未在用户 Windows 上运行完整仓库 pytest；未启动游戏或真实输入；未调用付费模型；未进行 GPU 训练或多显示器/UIPI 验证；本地完整 git clone 因网络解析失败未完成。源码审阅是跨模块、按安全关键链路深入，不是所有文件所有行的完整覆盖。未深入的部分在第 12 章列出，D00 必须补齐全文件台账。

隔离检查 `source_excerpt_probes.py` 与结果 JSON 已随交接包附带。`reproduced=true` 表示**该旧行为在指定隔离条件下重现**，不是“修复已通过”，也不是整套应用已经复现该风险。EX06/EX08 等是选定谓词/分支重放，不替代完整监督器集成用例。

### 1.3 当前CI基线：必须纠正文档与提交说明

Actions run `35062922806` 指向本基线，结论为 **failure**。Python 日志汇总为 **1 failed / 504 passed / 8 skipped**，共 513 项；这不是在容器里运行的 pytest 结果。[S39]

失败项是：`tests/windows/test_release_bundle.py::ReleaseBundleLauncherTests::test_clean_bundle_launches_agent_with_pinned_native_library`。

启动返回码 0 及 launched 断言已通过；失败来自 DLL 路径的短目录名 `RUNNER~1` 与长目录名 `runneradmin` 的字符串比较。应修文件身份比较，不能据此判定启动器根本启动失败，也不能据提交说明说当前 CI 全绿。[S28]

Rust 与 UI 作业成功并不能覆盖 Python-Native 集成。8 个 skip 包含 5 项 DLL 未构建、2 项双屏前提、1 项显式物理输入前提。Python 与 Rust 在不同作业，当前 CI 没有把 DLL 传给 Python 作业。[S29] [S39]

### 1.4 GLM‑5.3‑Flash 的角色

本计划默认把 GLM‑5.3‑Flash 作为**开发执行者**。把项目运行时 VLM 也换成该模型是独立的条件任务 D09，不应混在修 bug 里强制迁移。

2026-09-16 查阅的官方文档给出的 Model Code 是 `glm-5.3-flash`，并明确 `thinking.type` 仅支持 `enabled`。现有启动脚本固定传 `--vlm-no-thinking`，因此运行时迁移不能只替换模型名。当前默认 glm-4.6v 不因此自动构成这个兼容性 bug。[S40] [S27]

---

## 2. 项目全链路与现有能力

### 2.1 真实运行主链

```text
apps.agent CLI / scripts/run_mumu_autoplay.ps1
  → 加载GameProfile，定位唯一目标WindowIdentity
  → 选择WGC / DXGI / GDI，启动CaptureHub
  → 最新帧与观察/感知（OCR、页面、目标事实）
  → ModeRouter + GroundedVlmPlanner / ScriptedTapPolicy
  → ClosedLoopSupervisor（确认、重定位、恢复、效果观察）
  → GuiActionController / ActionChunkController
  → ActionArbiter + ControlLeaseManager
  → ActionScheduler
  → InputExecutor + FocusGuard
  → SendInput / 其他InputBackend
  → 回到捕获与效果验证
```

记录支线是 EpisodeWriter、视频、timeline/actions/observations/provenance/planner；数据支线是 Replay、DatasetProcessor、Manifest/质量校验、训练、模型 artifact、benchmark、qualification 与发布。**问题集中在这些支线的接缝，而不只是单一模型文件。** [S01] [S02] [S03] [S18] [S19] [S20]

### 2.2 应保留的现有设计

租约的代数校验、过期动作拒绝、唯一物理输入执行器、窗口复合身份、FocusGuard、锁存式 SafetyShutdown、独立 Watchdog 接口、事务性 Episode 发布、数据集 split 隔离、哈希锁文件和固定 Actions 引用，均应作为修复基础。不要为了“跑通”删除它们。[S05] [S06] [S07] [S09] [S10] [S11] [S18] [S20] [S29]

两个 Dashboard 是不同实现：通用命令看板带 CSRF/Host 等边界，独立入口当前接 DisconnectedOperatorControl；真实闭环的 DecisionDashboard 是本机只读画面/事件看板。不能把“本机只读没有用户登录”当成一个必须增加 OAuth 的 bug。[S23] [S24] [S25] [S26] [S37]

训练 CPU 基线与 VeOmni 协议包装是已有能力，不等于五阶段真实 GPU 训练。状态文档明确正式数据、模型及部分硬件资格尚未完成；旧修复审计里已经修复的 YAML 布尔、窗口选择等问题不得未经重查再当作现存缺陷。[S21] [S22] [S31] [S34]

---

## 3. 缺陷、风险和资格缺口台账

### 3.1 证据与优先级

- **C**：直接读取到的 CI 运行/日志事实。
- **S**：固定提交源码控制流可以确认的行为。
- **I**：本交接包有源码片段/分支隔离检查；并非完整应用或硬件测试。
- **R**：有实现依据的风险，但实际故障仍需故障注入/实机验证。
- **D**：官方模型文档与代码配置的兼容性对照。

**P0**：可能破坏物理输入安全边界；开始真实自动操作前必须关闭。
**P1**：影响闭环正确性、执行证据、可靠性或正式交付资格。
**P2**：诊断、性能、可维护性和运维体验；仍须有明确验收。

P0/P1 是本项目开发排程优先级，不是 CVSS 分数，也不代表已经发生安全事件。

| 编号 | 优先级/证据 | 问题 | 修复任务 |
|---|---|---|---|
| F01 | P0 · S | 真实运行入口未接入锁存急停和独立看门狗 | D02、D03、D16 |
| F02 | P0 · S | ui_back/ui_close/ui_promote 字符串被当成信任依据 | D03、D12 |
| F03 | P1 · S+I | 禁点校验点与最终点击点不同 | D03 |
| F04 | P1 · S+I | 效果验证提前返回可能使 pending 永不超时 | D05 |
| F05 | P1 · S+I | 恢复预算不覆盖 BACK；0 也不等于禁用恢复 | D06 |
| F06 | P1 · S | continuous 对终止状态统一重建监督器 | D02、D06、D13 |
| F07 | P1 · S+I | region_digest 对部分颜色变化失明 | D05 |
| F08 | P1 · S | 任务记忆 generation 与执行任务代数未统一 | D07 |
| F09 | P1 · S+I | 无外部证据配置时可仅凭两次高置信 DONE 确认完成 | D05 |
| F10 | P1 · S | 429 异常分类与新闭环调用者不匹配 | D08 |
| F11 | P1 · S+I | 本地结构化校验比声明的 Schema 宽松 | D08、D09 |
| F12 | P1 · S | 排队被当成真实动作；观察关联可能过旧 | D04、D10、D14 |
| F13 | P2 · S | 正常成功回复未及时更新 last_raw_reply | D10 |
| F14 | P2 · S+I | 高分辨率恢复未真正提高总览分辨率 | D05、D09 |
| F15 | P1 · S/R | 会话持久化无作用域、无原子替换、无恢复后证据门槛 | D07 |
| F16 | P2 · S/R | 持续运行统计无限增长，录制阻塞采集发布 | D11 |
| F17 | P1 · C | 当前 CI 的 Windows 路径字符串断言误报 | D01 |
| G18 | P1 · C/S | 原生构建成功不等于 Python-Native 接口测试执行 | D01、D16 |
| R19 | P1 · R | 模型网络出口、秘密与隐私边界不够显式 | D08、D09、D10、D13 |
| R20 | P1 · R | 低风险快规则缺少页面上下文授权 | D03、D12 |
| R21 | P1 · R | 原生调用存在缺少外层期限的阻塞边界 | D16 |
| G22 | P1 · S | 训练/发布的正式能力与证据尚未完成 | D14、D15、D18 |
| C23 | P1 · D/S | 直接把运行时换成 GLM-5.3-Flash 会遇到思考参数不兼容 | D09 |

### F01 · 真实运行入口未接入锁存急停和独立看门狗

**优先级/证据：** P0 / S。
**代码/日志事实：** request_stop() 只设置 stop 事件；SafetyShutdown、RuntimeWatchdog 已存在但未在 apps/agent/run.py 的真实组合路径使用。step() 在等待推理之后仍可继续授权、提交和主动 tick。[S02] [S03] [S05] [S06]
**触发条件：** 推理/验证进行中按急停或到达运行时长；停止循环与未结束 step 交错。尚未进行 Windows 竞态复现。
**后果与边界：** 不能用“停止事件已设置”证明输入已经失权；存在停止后的新动作提交窗口。
**对应任务：** D02、D03、D16。

### F02 · ui_back/ui_close/ui_promote 字符串被当成信任依据

**优先级/证据：** P0 / S。
**代码/日志事实：** assess() 对这些标签在存在任一相关热点配置时提前返回 EXECUTE；主循环还跳过这些标签的 validate_execution_frame。模型 action.target_label 是自由字符串。[S02] [S04] [S12] [S14]
**触发条件：** 模型返回保留标签但任意坐标，或推理后窗口/几何已改变。该分支并非毫无前置检查，但绕过了后续统一动作校验。
**后果与边界：** 规则、模型和恢复路径的安全保证不一致；标签可导致不经完整新鲜度、禁点和风险检查的执行路径。
**对应任务：** D03、D12。

### F03 · 禁点校验点与最终点击点不同

**优先级/证据：** P1 / S+I。
**代码/日志事实：** ActionValidator 校验 target_box.center；to_gui_action 最后再叠加 pointer_offset 并 clamp。EX06 重放了中心安全、最终点进入禁区的几何条件。[S04] [S12] [S14]
**触发条件：** 配置/规则为目标框增加非零偏移；已有协议勾选和物品格规则使用偏移。
**后果与边界：** 中心在安全区域不代表实际点击位置安全。
**对应任务：** D03。

### F04 · 效果验证提前返回可能使 pending 永不超时

**优先级/证据：** P1 / S+I。
**代码/日志事实：** observe() 的像素稳定候选分支、部分锚点分支在总超时判断之前返回。EX08 对持续变化的候选分支进行了隔离重放。[S04]
**触发条件：** 目标持续闪烁、OCR 不再锚定目标，且没有可确认的语义变化。
**后果与边界：** 动作长期处于 effect_pending，规划被阻塞；表面仍有画面但不继续推进。
**对应任务：** D05。

### F05 · 恢复预算不覆盖 BACK；0 也不等于禁用恢复

**优先级/证据：** P1 / S+I。
**代码/日志事实：** _request_recovery 只在首次高分辨率恢复检查 _max_recoveries；后续 BACK 分支不消耗这个预算。EX03 在 max_recoveries=0 且 profile 允许 back 时仍产生 BACK。[S04] [S03]
**触发条件：** 配置允许 back，且遇到无效动作或循环。
**后果与边界：** CLI 配置语义与实际恢复上限不一致。
**对应任务：** D06。

### F06 · continuous 对终止状态统一重建监督器

**优先级/证据：** P1 / S。
**代码/日志事实：** 主循环在 continuous 下对终止监督器使用 factory 重建，局部计数重置；没有按安全阻断、需人工处理、暂态失败分别决定是否恢复。[S02] [S03] [S27]
**触发条件：** 反复 BLOCKED/FAILED，或启动脚本遇到非零退出无限重启。
**后果与边界：** 单轮有界不代表整次运行有界；安全原因可能变成重复尝试，增加误操作或费用风险。
**对应任务：** D02、D06、D13。

### F07 · region_digest 对部分颜色变化失明

**优先级/证据：** P1 / S+I。
**代码/日志事实：** ROI 字节拼接后执行 `[::16]`；在 BGRA/RGBA 中只能抽到一个颜色通道及部分像素。EX01 改变整块区域的两个其他颜色通道，摘要完全相同。[S04]
**触发条件：** 按钮状态主要通过未采样的颜色通道变化，或变化恰好位于漏采像素。
**后果与边界：** 新鲜度/效果判断可能漏报；这是抽样算法的确定性盲区，不等于每帧变化都会漏报。
**对应任务：** D05。

### F08 · 任务记忆 generation 与执行任务代数未统一

**优先级/证据：** P1 / S。
**代码/日志事实：** GameSessionState 自行维护任务 generation；RealtimeAgentLoop 的 _task_generation 主要随 continuous 重建增加，未直接采用任务记忆变化。模糊同任务判断也可能合并不同数字目标。[S02] [S15]
**触发条件：** 推理期间主线切换，或“达到10级”切成“达到20级”等相似文本。
**后果与边界：** 旧任务规划结果不一定被任务代数检查识别；恢复的旧记忆可能错误引导导航。
**对应任务：** D07。

### F09 · 无外部证据配置时可仅凭两次高置信 DONE 确认完成

**优先级/证据：** P1 / S+I。
**代码/日志事实：** GoalVerifier._consider_snapshot 在 observed/fallback 皆空时仍满足 consistent；不同帧、间隔足够、置信度足够即可 True。EX02 重现。显式 required_evidence 路径不是这个缺口。[S04] [S12]
**触发条件：** 未配置 goal-evidence，模型连续给出 DONE；compact 路径会把 DONE 映射为 succeeded。
**后果与边界：** “模型声称成功”与“可验证目标已达成”没有在默认路径中区分。
**对应任务：** D05。

### F10 · 429 异常分类与新闭环调用者不匹配

**优先级/证据：** P1 / S。
**代码/日志事实：** 客户端抛 VisionRateLimitedError(Exception)，主闭环的模型调用捕获 BackendUnavailableError；GroundedOutcomeVerifier 同样未捕获该独立异常。[S13] [S12] [S02]
**触发条件：** 主模型或验证器返回 HTTP 429。
**后果与边界：** 本应可退避的限流可能传播到 TaskGroup 并结束运行；401/403 等还需从普通暂态重试中分离。
**对应任务：** D08。

### F11 · 本地结构化校验比声明的 Schema 宽松

**优先级/证据：** P1 / S+I。
**代码/日志事实：** 坐标用 float() 强制转换且混合坐标被整体除1000；验证器只做 float(confidence)>=threshold，未检查实际数值类型、有限性和上界。EX05、EX07 重现相关谓词。[S12] [S14]
**触发条件：** 布尔置信度、字符串数值、Infinity、超过1的置信度、混合[0,1]与[0,1000]坐标。
**后果与边界：** 接口声称 strict 不等于消费端严格；不合法输出可能通过局部判定。
**对应任务：** D08、D09。

### F12 · 排队被当成真实动作；观察关联可能过旧

**优先级/证据：** P1 / S。
**代码/日志事实：** arbiter accepted 后即 start_action，再 scheduler.tick；不存在贯穿监督器的逐动作执行回执。GUI 提交仍使用推理前 observation_id，而 DatasetProcessor 默认允许的动作-观察延迟是 1 秒。[S02] [S04] [S07] [S08] [S19]
**触发条件：** 执行器最终拒绝、部分物理原语失败、慢模型推理超过 1 秒。
**后果与边界：** 错误效果归因、看板过度宣称物理动作、训练样本关联无效或被处理器拒绝。
**对应任务：** D04、D10、D14。

### F13 · 正常成功回复未及时更新 last_raw_reply

**优先级/证据：** P2 / S。
**代码/日志事实：** GroundedVlmPlanner.decide 正常首次调用成功路径没有赋值 last_raw_reply，只有回退/修复等路径赋值；_record 使用该字段。[S12]
**触发条件：** 正常有效模型回复，尤其紧随上一次修复或规则快路径。
**后果与边界：** 看板/日志显示空摘要或旧回复，影响调试和证据可信度。
**对应任务：** D10。

### F14 · 高分辨率恢复未真正提高总览分辨率

**优先级/证据：** P2 / S+I。
**代码/日志事实：** _images 的 high_resolution_retry 只影响裁剪 padding；启动脚本 target_crops=0 时重试的编码参数相同。EX04 对编码调用参数重现。[S12] [S27]
**触发条件：** 当前 640 宽、无目标裁剪的启动配置进入 HIGH_RESOLUTION。
**后果与边界：** 消耗一次恢复机会却未提供更清晰证据。
**对应任务：** D05、D09。

### F15 · 会话持久化无作用域、无原子替换、无恢复后证据门槛

**优先级/证据：** P1 / S/R。
**代码/日志事实：** 真实入口使用共享 session_state.json；加载无版本/大小/来源绑定，写入直接 write_text，错误被吞掉；恢复状态会重新进入规划上下文。[S03] [S15]
**触发条件：** 多 profile/多窗口、进程崩溃时写入、只读安装目录、损坏或过时状态。
**后果与边界：** 串任务、静默丢失记忆、旧状态误导；实际文件损坏及多进程冲突尚未实机复现。
**对应任务：** D07。

### F16 · 持续运行统计无限增长，录制阻塞采集发布

**优先级/证据：** P2 / S/R。
**代码/日志事实：** CaptureHub._gaps_ns 持续 append，stats 每次对全量排序；_publish 在发布锁内先同步 record_frame。EpisodeWriter 有资源上限，不能误称它也无界。[S16] [S18]
**触发条件：** 长时间运行、频繁看板刷新、编码或磁盘变慢。
**后果与边界：** 内存/排序成本随时长增长，主源与回退源都可能被录制拖慢。
**对应任务：** D11。

### F17 · 当前 CI 的 Windows 路径字符串断言误报

**优先级/证据：** P1 / C。
**代码/日志事实：** Actions run 35062922806 的 Python 日志：1 failed / 504 passed / 8 skipped；失败用例前面的返回码 0 和启动标记断言已通过，DLL 路径仅短名/长名表示不同。[S39] [S28] [S29]
**触发条件：** 临时路径包含 RUNNER~1，PowerShell 解析为 runneradmin。
**后果与边界：** 测试阻断后续 smoke、依赖清单和打包检查，不能据此说当前构建已通过。
**对应任务：** D01。

### G18 · 原生构建成功不等于 Python-Native 接口测试执行

**优先级/证据：** P1 / C/S。
**代码/日志事实：** CI 的 Rust 和 Python 为独立作业；Python 作业未构建/接收 DLL。当前日志有 5 项原生测试因 DLL 未构建跳过，另有 2 项双屏和 1 项物理输入跳过。[S29] [S39]
**触发条件：** 默认 CI 运行。
**后果与边界：** 跨语言边界缺少持续门槛；硬件跳过必须与普通测试通过分开展示。
**对应任务：** D01、D16。

### R19 · 模型网络出口、秘密与隐私边界不够显式

**优先级/证据：** P1 / R。
**代码/日志事实：** 客户端接受配置的 base_url、使用默认 urllib 请求行为，未显式限定远端 HTTPS、允许目标和重定向策略；错误正文会进入异常。截图、OCR 和日志也可能包含隐私。[S13] [S03] [S27] [S35]
**触发条件：** 错误端点配置、重定向、上游回显、错误日志或敏感界面截图。
**后果与边界：** 需要本地假服务和脱敏测试验证，不能据此宣称已发生泄露。vision-mode local 并不强制本地网络出口。
**对应任务：** D08、D09、D10、D13。

### R20 · 低风险快规则缺少页面上下文授权

**优先级/证据：** P1 / R。
**代码/日志事实：** 通用“确定/提交”等 OCR 快规则以 LOW 风险生成动作；登录协议状态也在提案时置为已点，而非收到执行/效果确认之后。[S12] [S04] [S15]
**触发条件：** 同名确认按钮出现在交易、协议、删除、登录等不同语境。
**后果与边界：** 仅凭目标标签/高置信度不足以授予关键操作权限；是否能在真实画面通过所有保护需补反例测试。
**对应任务：** D03、D12。

### R21 · 原生调用存在缺少外层期限的阻塞边界

**优先级/证据：** P1 / R。
**代码/日志事实：** FFI create/capture/destroy 使用通道 recv、send 和线程 join；传入 capture timeout 不自动约束初始化、驱动阻塞及关闭整个生命周期。[S30]
**触发条件：** 驱动/API 卡死、初始化不返回、采集与关闭竞争。
**后果与边界：** 实机尚未观察到挂死；需故障注入确认关闭上界及是否需要进程隔离。
**对应任务：** D16。

### G22 · 训练/发布的正式能力与证据尚未完成

**优先级/证据：** P1 / S。
**代码/日志事实：** BehaviorCloningTrainer 明确是确定性可行性/契约基线，按钮为全局多数掩码；VeOmniBackend 仅包装注入的 launcher。状态文档明确五小时数据和五阶段真实 GPU 训练未完成。[S21] [S22] [S31] [S32]
**触发条件：** 把 smoke checkpoint、接口 Protocol、历史报告当作正式训练或 V1 完成。
**后果与边界：** 属于能力/资格缺口，不是应删除的现有基线。正式阶段需独立实现与证据。
**对应任务：** D14、D15、D18。

### C23 · 直接把运行时换成 GLM-5.3-Flash 会遇到思考参数不兼容

**优先级/证据：** P1 · D/S。
**代码/日志事实：** 2026-09-16 查阅的官方文档给出 model code glm-5.3-flash，thinking.type 仅支持 enabled；当前脚本固定 --vlm-no-thinking。[S40] [S27] [S13]
**触发条件：** 用户另外选择把运行时视觉模型迁移到该模型。当前默认 glm-4.6v 不因此自动成为缺陷。
**后果与边界：** 需要模型能力配置和独立兼容性测试，不能只替换模型名；本次未调用付费 API。
**对应任务：** D09。

---

## 4. 目标、不变量和交付层次

### 4.1 不可削弱的八个不变量

| 不变量 | 必须成立的行为 |
|---|---|
| 输入失权 | 急停锁存后不再有新的物理 submit；旧推理、重试和重建监督器不能重新 arm。 |
| 单一安全门 | 模型、OCR 快规则、热点、恢复都经过同一最终检查，来源标签不是权限。 |
| 上下文绑定 | 运行、窗口、几何、任务、profile 或期限任一变化都不能给旧动作“换皮续命”。 |
| 真实执行 | queued/accepted 不是 executed；效果验证与训练标签必须关联实际执行回执。 |
| 有界等待 | 每次推理、验证、效果等待、恢复和关闭都有独立总期限与计数预算。 |
| 可验证成功 | 模型 DONE 不直接等于任务成功；没有外部目标证据不能写 SUCCESS。 |
| 可信数据 | 记录、观察、动作、许可证、数据 split 及 checkpoint 都可追溯且不可伪造。 |
| 资格诚实 | 当前代码的资格来自当前代码的证据；历史 smoke、缺环境的 skip 或接口占位都不是正式 PASS。 |

### 4.2 分开交付三种结果

**A. 安全运行时开发候选：** 完成 D00—D14 中运行时相关部分、D16、D17 和 D19；解决 P0 及运行时 P1。尚未训练完成也可以明确交付候选，但不能宣称正式 V1。
**B. 可训练的数据闭环：** 执行回执、Episode 和 Dataset 一致，完成实际质量审查与至少 5 小时语料。合成/Fixture 开发数据必须与正式采集语料分开标识。
**C. 正式 V1 资格：** D15 与 D18 完成，真实模型、held-out 场景、硬件和安装/回滚证据完整。安全运行时修好并不自动完成 C。

### 4.3 明确不做的事

不绕过 UIPI/权限边界或第三方服务规则；不把密码、API Key 写入配置或日志；不自动同意账号协议/填写实名资料/充值支付；不对用户游戏执行故障注入；不使用历史交接文档里的授权替代本次授权；不擅自推送代码、创建发布或消费付费 API 额度。

这些限制不妨碍在本地 Mock 和自有 Fixture 完成绝大部分开发与回归。

---

## 5. 目标接口与执行流程

下面是**建议新增/改造的契约草案**，不是仓库现有 API，也不是本次已提交代码。

### 5.1 三类核心对象

```python
@dataclass(frozen=True)
class ActionExecutionContext:
    run_id: str
    run_generation: int
    target_identity: WindowIdentity
    task_generation: int
    geometry_generation: int
    profile_hash: str
    inference_frame_id: str
    validated_frame_id: str
    validated_at_ns: int
    expires_at_ns: int

@dataclass(frozen=True)
class ExecutionReceipt:
    action_id: str
    decision_id: str
    primitive_id: str
    status: ExecutionStatus       # primitive: EXECUTED / REJECTED / EXPIRED / FLUSHED
    executed_at_ns: int | None
    failure_reason: str | None
    context: ActionExecutionContext

@dataclass(frozen=True)
class GoalEvidence:
    predicate_id: str
    frame_ids: tuple[str, ...]
    task_generation: int
    evidence_payload: Mapping[str, object]
    confidence: float
    verified_at_ns: int
```

逻辑动作的 COMPLETE/PARTIAL/NOT_EXECUTED 由多个原语回执聚合；原语计数不额外重复计入 PARTIAL。

可信 DecisionSource 由运行时代码赋值，不允许模型 JSON 自行声明“我是规则所以免检”。这只是受信任进程内的执行契约，不宣称能够防御任意恶意 Python 代码在同进程执行。

### 5.2 改造后的时序

```text
捕获/感知
 → 固定请求context，发起模型或规则提案
 → 推理返回（可能很晚）
 → 检查run未停止、请求尚未过期
 → 取得新鲜帧，更新任务/页面状态
 → 保留旧request context，与当前context比较
 → 解析最终坐标（包含偏移）
 → ActionSafetyGate完整检查
 → 写execution_observation
 → arbiter/lease授权及scheduler排队
 → executor最终校验并实际提交
 → 发ExecutionReceipt
 → 从执行回执时刻开始有界效果验证
 → GoalEvidence/Progress更新
 → Episode/看板/数据集消费同一事实
```

严禁两种伪修复：把旧结果的 generation 改成当前 generation；把旧观察的 timestamp 改成当前时间。它们会掩盖失效，而不是修复失效。

### 5.3 终止与恢复状态

```text
STARTING → OBSERVING ↔ PLANNING → VALIDATING → EXECUTING → VERIFYING
                 ↘ MANUAL_REQUIRED / BLOCKED / FAILED / SUCCEEDED
任意状态 → SAFETY_TRIPPED → NEUTRALIZED → STOPPED
```

`SAFETY_TRIPPED` 不允许 continuous 或模型输出复位。`MANUAL_REQUIRED` 不由超时自动解除。暂态重试有 run 级预算，正常任务的下一周期不是“清空所有失败历史”。

---

## 6. 里程碑、依赖和合并顺序

| 里程碑 | 主要任务 | 出口条件 |
|---|---|---|
| M0 基线可信 | D00、D01 | 固定 SHA、覆盖台账、CI 误报修复、Native 测试不再假通过 |
| M1 输入安全 | D02、D03 | 急停锁存与统一动作门的所有负例通过；此后才能进入受监督物理测试 |
| M2 闭环可信 | D04、D05、D06、D07、D12 | 回执、效果、目标、恢复、任务代数一致 |
| M3 模型可靠 | D08；运行时选用 GLM 时执行 D09 | 类型/能力/错误/预算完整，不靠猜输出 |
| M4 长稳与运维 | D10、D11、D13、D16 | 看板事实正确、资源有界、Windows 边界验证 |
| M5 数据与训练 | D14、D15 | 正式数据和五阶段训练证据存在 |
| M6 候选及发布 | D17、D18、D19 | 开发候选和正式 V1 分开报告，发布证据同 SHA |

**依赖规则：** D09 是运行时 GLM 迁移的条件任务；D17 仅在启用该 provider 时额外要求 D09 通过。D19 可以先为运行时候选交接；D18 未完成时文档必须保留 V1 未完成状态。

**允许并行：** D01 的 CI、D08 的传输 Mock、D02 的停机可以在接口先冻结后分别工作。
**应串行：** D03/D04/D05/D06/D12 大量修改 `closed_loop.py` 与 `agent_loop.py`，不得多个执行者同时各自重写整文件。先提取契约，再小步合并，逐次跑集成测试。

不在本计划中承诺工期。每个里程碑以证据门槛而非“写完几个文件”结项。

---

## 7. 文件级开发任务卡

每张任务卡是一个逻辑交付单元，可拆多个小 PR，但不得把实现、回归测试和验收文档拆到无法追溯。`existing_files` 包括现存目录范围；`new_files` 为建议新增名称，实施前通过 D00 确认没有等价模块可复用。

### D00 · 冻结审计基线并完成全文件覆盖台账

**优先级：** P0。 **前置：** 无。 **对应条目：** 全局治理/验收。

**目标：** 让后续工作建立在可追溯的源码、环境和测试结果上，补齐本次未逐函数检查的代码面；不把文档宣称当作运行结果。

**修改现有文件/目录：** `ARCHITECTURE.md`；`GLM53_HANDOFF_PLAN.md`；`docs/status/uga-v1-status.md`；`RELEASE_CHECKLIST.md`；`pyproject.toml`。
**建议新增：** `docs/reviews/audit-coverage-2026-09-16.md`；`docs/reviews/bug-ledger-2026-09-16.md`。

**具体修改方式：**

1. 在独立工作分支记录 git rev-parse HEAD、git status --porcelain、Python/Node/Rust/Windows/驱动信息。基线必须是 34eba5e...；若 HEAD 已变化，先记录差异再重查受影响结论，不直接覆盖用户改动。
2. 运行 git ls-files，将所有可执行源文件按 CLI、core、windows/native、capture、control/safety、perception/policy/agent、recording、dataset、training/models、benchmark/evaluation、release、ui、tests 分类。每文件写入审阅范围、核心不变量、调用者/被调用者、已有测试、未审阅部分。
3. 先复跑不触发真实输入的基线门槛，完整记录 passed/failed/skipped 及原因。CI 日志与本机日志分别标注，不合并计数。
4. 将本报告 F/G/R/C 编号导入缺陷台账。每个条目保留：源文件/符号、触发条件、证据级别、复现命令、预期行为、修复提交和关闭证据。未复现风险仍标 R，禁止改写为“已证明漏洞”。
5. 旧交接文档中的授权、绝对机器路径和历史证据只作参考；本次未授权真实输入、账号协议接受、交易、发布或密钥修改。

**回归测试：** 基线清单与 git ls-files 对照无漏掉的可执行文件；vendor/generated 文件单列。每个 P0/P1 至少关联一个真实测试入口或明确的待硬件验证条目。

**验收：** 全部代码面有台账，所有未审阅/未运行项明确标出；不得用“全项目无问题”替代台账。

**失败/回滚策略：** 仅添加审计文件和基线日志，不改变运行行为。

**交付物：** audit-coverage、bug-ledger、environment.json、baseline-test-results、read-only diff。

### D01 · 修复 CI 误报并接通 Native 测试产物

**优先级：** P1。 **前置：** D00。 **对应条目：** F17、G18。

**目标：** 恢复可信的持续集成，明确“测试真的执行了”而不是仅通过编译或跳过。

**修改现有文件/目录：** `tests/windows/test_release_bundle.py`；`.github/workflows/ci.yml`；`scripts/build_native.ps1`；`pyproject.toml`。

**具体修改方式：**

1. 解析 .launched.env 中 DLL= 和 SHA= 字段；使用 Path(actual).samefile(expected)（两者存在时）比较文件身份，独立校验 SHA256。不能仅 lower()、删除路径断言或把失败改成 skip。
2. 新增短路径/长路径、大小写、带空格路径用例；保留篡改 DLL、错误 manifest anchor 拒绝等现有负例。
3. 新增 native-python-integration 作业：在同一受控环境构建 uga-capture，设置 DLL 路径和期望 SHA 后执行相关 Python 测试；或由 Rust 作业上传 DLL、Python 依赖作业下载并校验。不能假设不同作业共享目录。
4. 分离纯单元测试、Windows 非物理集成、需要桌面/双屏/物理输入的测试。自动 CI 不得为了消除 skip 而注入真实游戏输入。
5. 用 if: always() 上传 junit/日志/版本/skip 原因；保持所有 Actions 固定 commit、contents:read、persist-credentials:false。新增依赖重新生成受审查的哈希锁文件。
6. 支持矩阵从项目声明的 3.11 及文档推荐 3.12 开始；Node/Rust 采用仓库锁定值。PS5.1/PS7 均验证启动脚本；不要无依据升级全部依赖。

**回归测试：** test_bundle_env_uses_same_file_for_short_and_long_paths；test_bundle_wrong_dll_identity_rejected；test_native_integration_requires_built_dll；测试正常发布包启动、篡改包拒绝、安装后命令 smoke。

**验收：** 当前失败用例恢复；所有非硬件门槛真实执行；Native 缺失在专门作业中为 FAIL 而非 SKIP；物理/双屏需求如实保持待验证。

**失败/回滚策略：** CI 调整和测试修复单独提交；若 artifact 传递不稳定，回退为同作业内构建，不能回退为忽略 Native 测试。

**交付物：** CI 链接、junit.xml、skip-matrix.json、安装/打包日志。

### D02 · 接入统一锁存急停、看门狗与停机协议

**优先级：** P0。 **前置：** D00。 **对应条目：** F01、F06。

**目标：** 急停失权不依赖模型推理、磁盘、日志或正常任务退出；停止后不得再授予可执行控制权。

**修改现有文件/目录：** `apps/agent/run.py`；`uga/core/agent_loop.py`；`uga/safety/shutdown.py`；`uga/safety/watchdog.py`；`uga/safety/emergency_stop.py`；`uga/control/scheduler.py`；`uga/control/executor.py`。
**建议新增：** `uga/core/run_context.py`。

**具体修改方式：**

1. 组合根复用 SafetyShutdown、RuntimeWatchdog 和 RuntimeWatchdogMonitor。AgentEnableState 初始 False；目标/profile/后端/急停注册/看门狗都就绪后才显式 arm。
2. 急停回调先调用安全锁存：禁用→撤销租约→清队列→释放输入，然后通知 asyncio stop。打印、写日志和关闭 HTTP 服务必须排在输入失权之后。
3. 新增 RunContext，包含 run_id、单调递增 run_generation、取消标志和 tripped 原因。所有推理结果携带发出时 generation；每次 await 返回、授权前、入队前和最终写入前复核。
4. 定义输入提交与 trip 的同步边界和锁顺序。以“网关确认锁存完成”为线性化点：此后新 submit 必须为 0；已经被 OS 接收的历史事件不可宣称能撤回。
5. 运行时长耗尽、SIGINT、业务终止、TaskGroup 异常统一进入停机协议，但原因分类独立。幂等清理保留原始异常及 cleanup_errors。
6. 看门狗监控控制/调度活性而不是把正常慢模型直接当卡死；心跳不得来自一个不检查控制健康的盲定时器。连续模式和进程自动重启不得清除安全 trip。
7. 取消 to_thread 的等待不等于终止底层调用。先保证迟到结果永远无权执行；再对网络、采集和关闭建立有限等待及必要的进程隔离，禁止销毁仍在使用的 DLL 句柄。

**回归测试：** test_stop_during_planning_cannot_submit_late_action；test_stop_between_assess_and_enqueue；test_stop_between_guard_and_backend_commit；test_deadline_during_verifier_neutralizes_immediately；test_watchdog_trips_when_scheduler_stalls；test_continuous_cannot_clear_safety_trip；1000 种可重放调度交错；时钟/queue.flush/backend.release 抛异常仍尝试剩余清理。

**验收：** 锁存确认后新输入提交=0；安全停机不等待模型响应；模型线程迟到仅记录 discard。建议验收目标：受监督 Fixture 中热键到输入失权 P99≤100ms、到释放完成≤200ms；记录最差值及测试负载。它是待测目标，不是已达到的指标。

**失败/回滚策略：** 无法满足时默认禁用物理输入，仅允许 observe-only；不恢复旧的 stop 事件替代急停。

**交付物：** 停机状态图、竞态回归测试、急停/看门狗延迟原始事件。

### D03 · 建立所有动作共用的最终安全门

**优先级：** P0。 **前置：** D02。 **对应条目：** F02、F03、R20。

**目标：** 模型、OCR 规则、热点、恢复四条路径共享同一不可绕过的执行边界；校验的必须是实际落点。

**修改现有文件/目录：** `uga/agent/closed_loop.py`；`uga/core/agent_loop.py`；`uga/perception/schema.py`；`uga/gui/controller.py`；`uga/control/executor.py`。
**建议新增：** `uga/safety/action_gate.py`。

**具体修改方式：**

1. 移除按 target_label 直接放行以及主循环对保留标签跳过最终帧验证的分支。标签仅是显示文本，不能授予权限。
2. 新增只由可信 Python 工厂设置的 DecisionSource 与校准引用；不从模型 JSON 读取可信 source。热点引用须匹配 profile_hash、控件 ID、校准版本、页面前置条件。
3. 新增不可变 ActionExecutionContext：run_generation、完整 WindowIdentity、geometry_generation、task_generation、request_frame_id/seq、validated_frame_id/seq、证据时间、deadline、profile_hash。
4. 先解析动作、应用偏移、坐标转换/边界处理，得到最终 normalized/physical point，再检查 no_click、允许区域、遮挡、关键动作授权。不能只检查框中心。
5. 最终安全门固定顺序：运行已启用且未 trip→context 一致→帧绝对年龄与证据有效→目标与最终点有效→页面风险策略→租约/动作期限→提交；恢复和热点也必须经过。
6. 保留 FocusGuard/lease 作为最后一层 OS 安全，不假设它们能发现语义任务或旧画面不匹配。
7. 关键操作授权必须绑定动作类别、目标、当前证据和短期 nonce；模型自报 LOW、包含“确认”文本或高 confidence 不能代替操作者授权。

**回归测试：** test_reserved_ui_label_from_model_has_no_privilege；test_calibrated_exit_rejected_after_window_recreation；test_promote_rejected_in_no_click_region；test_pointer_offset_crosses_no_click；test_every_action_source_obeys_same_generation_guards；test_no_input_on_account_payment_delete_consent_without_approval。

**验收：** 所有实际执行路径经过统一门；每个 guard 负例对 model/rule/recovery 均拒绝；禁止以更宽帧年龄或更低置信阈值让测试通过。

**失败/回滚策略：** 热点无法证实控件身份时 ABSTAIN/WAIT；不重新启用保留标签白名单绕过。

**交付物：** ActionExecutionContext 契约、guard 路径覆盖表、安全负例测试。

### D04 · 引入物理执行回执与新鲜观察关联

**优先级：** P1。 **前置：** D03。 **对应条目：** F12。

**目标：** 分清提案、排队、执行成功、执行失败和效果确认，并保留动作与执行前观察的可追溯关联。

**修改现有文件/目录：** `uga/control/executor.py`；`uga/control/scheduler.py`；`uga/gui/controller.py`；`uga/core/agent_loop.py`；`uga/agent/closed_loop.py`；`uga/recording/schema.py`；`uga/recording/episode_writer.py`。
**建议新增：** `uga/control/execution_receipt.py`。

**具体修改方式：**

1. 定义 ExecutionReceipt：action_id、parent_decision_id、primitive_id/index、status、实际 executed_at 单调时间、目标身份、最终坐标、失败原因、lease/run/task/geometry 代数。
2. InputExecutor 返回结果后由 scheduler 发布回执；每个 primitive 恰有一个 terminal receipt。组合点击/长按的部分成功标 PARTIAL，不得整体写 executed。
3. 监督器只在收到所需物理原语的执行证据后进入效果 pending；效果 deadline 基于实际执行时刻，而不是模型完成或入队时刻。拒绝动作不进入效果成功判定。
4. 在最终 fresh 验证点写入新的 execution_observation；保留 inference_observation_id 用于追溯，但训练对齐使用 execution_observation_id 及执行时间。不得通过把旧观察时间改成当前时间伪造新鲜度。
5. 贯通完整 decision/action ID，不用截断后的 ID 作为唯一关联键。原始提案与监督器修改后的动作分别保存。
6. 新增 receipt 文件/表的版本化 schema，旧 Episode 保持不可变；旧版本缺执行回执的动作不得无条件用于正式模仿学习标签。

**回归测试：** test_arbiter_accept_executor_reject_has_no_effect_pending；test_partial_click_is_not_completed_click；test_all_primitives_have_terminal_receipts；test_slow_vlm_uses_fresh_execution_observation；test_scheduler_counter_conservation_with_guard_rejection。

**验收：** 逻辑动作成功必须有执行回执；推理 10 秒后仍能用新鲜观察生成合法训练关联；执行失败在看板和 Episode 均可见。

**失败/回滚策略：** 先以旁路新字段双写，校验后切换消费者；出现 receipt 缺失立即阻断动作，不回退为 accepted 即 executed。

**交付物：** receipt schema、事件关联测试、慢推理端到端 Mock Episode。

### D05 · 修复效果超时、图像差异和目标完成验证

**优先级：** P1。 **前置：** D04。 **对应条目：** F04、F07、F09、F14。

**目标：** 每个动作在有限时间内得到真实效果结论；完成任务必须有与目标相关的外部证据。

**修改现有文件/目录：** `uga/agent/closed_loop.py`；`uga/policy/grounded_vlm.py`；`uga/agent/progress.py`。
**建议新增：** `uga/agent/effect_verifier.py`；`uga/agent/goal_verifier.py`。

**具体修改方式：**

1. 先在旧类中修复，再提取模块以降低回归风险。所有 pending 分支共享绝对 deadline；像素/锚点候选可刷新稳定窗口，绝不能刷新总 deadline。
2. deadline 到达后统一转 INEFFECTIVE/UNKNOWN 并触发有界恢复；所有提前 return 前都检查硬 deadline。
3. 替换 ROI [::16] 为多通道空间降采样：对整块 RGB 或亮度+色度特征采样，忽略 alpha 及 stride 填充；规定相同尺寸/格式下的差异函数。增加多区域/页面稳定锚点，不把任意动画当语义进展。
4. 建立效果谓词注册：页面进入/离开、指定数字目标进度、指定控件状态、任务 generation 变化。模型 expected_effect 只是说明，不能直接等同验证结论。
5. GoalVerifier 必须同一 run/task/window/geometry 上下文、单调新鲜证据和至少两次不同采集证据。缺少明确目标谓词时返回 UNVERIFIED，不把高 confidence DONE 记 SUCCESS。
6. 显式区分观察任务和动作任务：观察任务可以零输入完成，但必须满足配置证据；动作任务还需关联必要执行回执。无 OCR 时只允许已定义并测试的视觉谓词，不用模型自述替代。
7. HIGH_RESOLUTION 真正提升有效像素：例如总览 640→1280，或对已识别目标增加 1 个较高清 ROI。记录前后尺寸/字节/裁剪；图像已达上限则标无法升级，走不同策略而不是假重试。

**回归测试：** test_effect_deadline_expires_under_permanent_pixel_animation；test_effect_deadline_expires_under_anchor_flicker；test_red_green_only_change_detected；test_alpha_and_padding_do_not_fake_progress；test_unrelated_animation_not_success；test_done_without_goal_evidence_is_unverified；test_two_frames_different_task_cannot_confirm；test_high_resolution_retry_increases_effective_detail。

**验收：** 每个 pending 在 deadline+一个观察周期内终结；EX01/EX02/EX04/EX08 转为期望行为；无证据成功数=0。

**失败/回滚策略：** 视觉证据不足时停在 UNVERIFIED/BLOCKED，禁止回退成“画面有变化即成功”。

**交付物：** 目标/效果谓词契约、固定反例集、deadline 与效果日志。

### D06 · 重构恢复预算与连续运行状态机

**优先级：** P1。 **前置：** D02、D05。 **对应条目：** F05、F06。

**目标：** 恢复有明确总上限，安全阻断不能被无条件自动恢复。

**修改现有文件/目录：** `uga/agent/closed_loop.py`；`uga/core/agent_loop.py`；`apps/agent/run.py`；`scripts/run_mumu_autoplay.ps1`。
**建议新增：** `uga/agent/recovery_budget.py`。

**具体修改方式：**

1. 建立 run 级 RecoveryBudget，由组合根持有，监督器重建不得重新创建预算。跟踪 per_action/per_state/per_run 次数、预算耗尽原因、冷却时间。
2. 明确普通任务导航 NAV_BACK 与失败恢复 RECOVER_BACK；只有为已失败动作解困的行为消耗恢复预算，禁止仅改名字绕过预算。
3. --max-recoveries=0 禁止所有恢复动作和高分辨率重试；1/2 覆盖每一种实际恢复。预算在真实恢复开始/执行回执时记账，定义失败请求是否计入模型调用预算。
4. 终止原因枚举：SUCCESS、USER_STOP、SAFETY_TRIP、MANUAL_REQUIRED、AUTH_FAILURE、TRANSIENT_FAILURE、RECOVERY_EXHAUSTED。仅白名单暂态原因能有限重试。
5. continuous 可推进新任务周期，但不能自动清除安全 trip、账号门禁或已耗尽 run 预算；进入 MANUAL_REQUIRED 后释放输入，直到明确重新授权。
6. 进程监督脚本采用指数退避+抖动+最大次数；非零退出一律重启是错的。区分退出码与结构化 termination 文件；持久化未确认动作，重启后先重新观察，不重放旧动作。

**回归测试：** test_zero_budget_blocks_back_and_high_resolution；test_one_budget_shared_across_recovery_types；test_budget_survives_supervisor_rebuild；test_safety_trip_never_auto_restarts；test_transient_restart_is_bounded；test_crash_after_mouse_down_requires_neutralization_before_rearm。

**验收：** EX03 不再返回 BACK；每次 run 可计算实际总恢复次数；每次自动重试都有可解释原因和剩余额度。

**失败/回滚策略：** 不明确原因时保持停止；回退为手动重启模式，而不是无限恢复。

**交付物：** 恢复状态图、退出码契约、run 级预算测试。

### D07 · 统一任务代数并安全持久化会话

**优先级：** P1。 **前置：** D03。 **对应条目：** F08、F15。

**目标：** 任务切换能够使旧推理立即失效，跨启动记忆不串 profile 且不能替代新鲜证据。

**修改现有文件/目录：** `uga/agent/session_state.py`；`uga/core/agent_loop.py`；`apps/agent/run.py`；`uga/perception/builder.py`。
**建议新增：** `uga/agent/session_store.py`。

**具体修改方式：**

1. 由 RunContext/任务协调器提供唯一 task_generation。任务身份改变时推进代数，撤销关联计划/租约并清理旧 pending；所有 request/snapshot/outcome 共享这一来源。
2. fresh perception 生成后也更新任务状态，再比较旧 request context；禁止给旧 outcome 改写 generation 以通过检查。
3. 结构化区分 quest identity 与 progress：目标对象/数量/等级改变算任务变化，0/1→1/1 等进度变化不盲目当新任务。模糊相似只能抗 OCR 抖动，不能抹掉关键数字差异。
4. 持久化路径使用可写 state-dir 并按 profile_hash+显式 session namespace 隔离；默认位于用户数据目录，避免向安装包目录写状态。
5. 定义版本化 JSON、64KiB 初始大小上限、字段数/长度/有限数值范围、schema_version、source_profile_hash、updated_at 与 TTL；所有默认上限写进配置说明。
6. 同目录临时文件→flush/fsync→os.replace；写失败产生可观察告警，不假装已保存；多实例加锁或拒绝共享同一 session namespace。
7. 恢复状态标 UNTRUSTED_RESTORED：可以显示、用于候选检索，但未被两张新鲜帧证实前不能触发操作。任务记忆 generation 恢复不能凌驾新 run 的 epoch。

**回归测试：** test_quest_goal_10_to_20_invalidates_old_request；test_ocr_jitter_keeps_identity_but_progress_changes；test_reloaded_memory_cannot_authorize_action；test_two_profiles_do_not_share_state；test_atomic_state_write_survives_interruption；test_oversized_corrupt_future_schema_state_is_rejected。

**验收：** 不存在两个互不一致的任务代数；旧请求回包稳定 discard；state 故障可观察且不造成输入。

**失败/回滚策略：** 状态损坏隔离为诊断文件并从空会话启动；不自动采信旧文本。

**交付物：** session schema、任务代数状态测试、读写迁移说明。

### D08 · 统一模型传输、严格解析与错误重试策略

**优先级：** P1。 **前置：** D00。 **对应条目：** F10、F11、R19。

**目标：** 模型服务故障与不合法输出不能导致越权、无限重试、失控开销或隐性凭据泄露。

**修改现有文件/目录：** `uga/policy/vlm_planner.py`；`uga/policy/grounded_vlm.py`；`apps/agent/run.py`；`uga/core/errors.py`。
**建议新增：** `uga/policy/structured_output.py`；`uga/policy/vision_transport.py`。

**具体修改方式：**

1. 定义 ProviderError 分类：rate_limit、auth、quota、timeout、transient5xx、invalid_request、unsupported_capability、malformed_output。主模型和验证器使用同一分类，不再漏接 VisionRateLimitedError。
2. 429 读取并有界处理 Retry-After，指数退避加抖动；401/403 停止并提示配置；配额耗尽停止；5xx/连接错误有限重试。所有调用共享每决策及每 run 调用/时间预算。
3. 增加整个决策的 total_deadline，包含 schema 能力回退、格式修复和验证器；它独立于每次 socket timeout。最多一次格式修复，不靠持续提高 max_tokens 无限修复。
4. 本地消费端严格验证完整/compact/verifier 三种 Schema：拒绝 bool 当 number、字符串强转、NaN/Infinity、额外字段、错误类型、过长文本、重复关键字段；confidence 必须有限且 [0,1]。
5. coord_space 作为 provider 适配配置或显式字段，整个 box 只能采用一种坐标系；禁止“有一个>1 就全除 1000”的猜测。校验后再构造 GroundedAction。
6. 远端默认只准 HTTPS，显式 allowlist 目的地；回环 HTTP 是单独例外。拒绝 URL userinfo、敏感 query 和跨 origin 重定向；API Key 只进入请求头。
7. 日志采用结构化错误码和脱敏摘要，限制响应正文/图像/日志大小；把原有 4MiB 响应上限保留下来。允许通过受控 credential resolver 更新密钥，但不打印或保存明文。
8. 停止不依赖 HTTP 调用被强制取消；迟到响应按 RunContext 丢弃。若总关闭预算仍被 urllib 线程拖住，使用独立单任务 worker 进程，而不是不安全地杀线程。

**回归测试：** 本地假服务：429 主模型/验证器、401、403、配额耗尽、500、超时、慢滴响应、跨域 302；test_verifier_rejects_bool_string_infinity_confidence；test_coordinate_spaces_cannot_mix；test_parser_rejects_extra_and_duplicate_fields；test_repair_budget_is_global_to_decision；test_error_log_never_contains_secret_sentinel。

**验收：** EX05/EX07 变成拒绝；429 不再杀死整个运行；auth 错误不会持续烧请求；总调用数/时间预算可测；测试完全使用假密钥和本地假端点。

**失败/回滚策略：** 保留旧 provider 兼容适配器但不能绕过严格消费端校验；未知 capability 必须显式失败而非静默降级。

**交付物：** provider 错误契约、strict schemas、本地 mock HTTP 测试。

### D09 · 增加 GLM-5.3-Flash 能力档案与条件式运行时接入

**优先级：** P1。 **前置：** D08、D05。 **对应条目：** C23、F14、R19。

**目标：** 明确 GLM 作为开发执行者与作为运行时 VLM 是两件事；只在选择迁移运行时后实施此兼容配置。

**修改现有文件/目录：** `scripts/run_mumu_autoplay.ps1`；`apps/agent/run.py`；`uga/policy/grounded_vlm.py`；`uga/policy/vlm_planner.py`。
**建议新增：** `configs/models/glm-5.3-flash.yaml`；`docs/guides/glm53-provider.md`。

**具体修改方式：**

1. 以 2026-09-16 官方能力文档为当前参考：model code 为 glm-5.3-flash，thinking.type 只支持 enabled。启动前拒绝该模型与 --vlm-no-thinking 组合，不能默默发 disabled。
2. 引入 provider/model capability profile，声明支持模态、thinking 策略、structured output 模式、图片尺寸预算、总 deadline 和输出预算；API 路径使用账号实际可用且已验证的官方配置，不把 Coding Plan 与普通 API 凭据混为一谈。
3. 配置默认不含任何真实 Key；保留 glm-4.6v 和本地 Qwen 可选，切换模型不改变安全门或成功标准。
4. 用固定无敏感信息 Fixture 图像做少量兼容探测：认证、单图/多图、JSON 模式、返回 content 完整性、finish_reason/usage 可观测。真实付费调用须单独获得当前授权并设置总额度。
5. 不要把 reasoning_content 当作可执行动作的备用正文；content 为空或未完成时返回 ABSTAIN/模型错误。max_tokens=256 是否足够必须用固定语料实测，不可按模型宣传直接认定。
6. 开发执行者使用本计划任务卡逐项完成，不需要为了“使用 GLM 开发”强制替换项目现有模型。

**回归测试：** test_glm53_rejects_disabled_thinking_at_startup；test_provider_profiles_do_not_change_action_safety；test_truncated_or_reasoning_only_reply_cannot_be_executed；许可后执行受预算控制的 Fixture 兼容探测并保留脱敏原始结果。

**验收：** 开发交接不依赖付费 API；运行时迁移时能力档案通过，unsupported 参数在发请求前报错；无真实 API 授权则状态为 NOT_RUN 而非 PASS。

**失败/回滚策略：** 回滚到已验证的 provider profile；不回退消费端严格校验及安全门。

**交付物：** GLM 能力配置、兼容性矩阵、预算报告（如经授权执行）。

### D10 · 修复动作证据、模型摘要和指标口径

**优先级：** P1。 **前置：** D04、D08。 **对应条目：** F12、F13、R19。

**目标：** 让看板、日志、Episode 和训练消费者对同一动作的事实描述一致。

**修改现有文件/目录：** `uga/policy/grounded_vlm.py`；`uga/policy/decision_journal.py`；`uga/agent/session_state.py`；`apps/agent/dashboard.py`；`uga/recording/schema.py`；`uga/recording/episode_writer.py`；`uga/control/scheduler.py`。

**具体修改方式：**

1. 每个推理请求开始清空 per-request 状态；所有成功响应先更新该 request 的 raw_reply，再解析/修复；修复另设 attempt 字段，不能沿用上次请求摘要。
2. 统一事件链：proposed→validated/rejected→scheduled→executed/partial/rejected/expired/flushed→effect_verified/ineffective/unknown；规则与模型都写 source，保留 proposed 和 final。
3. 指标使用分母明确的计数：scheduled=executed+rejected+expired+flushed+pending（按同一原语粒度）；guard 失败也增加 rejected，不能消失。
4. 区分 logical_actions、physical_primitives、verified_effect_actions、verified_goal_progress，禁止统称“成功动作”。
5. 主循环向只读看板发布不可变快照；不要在 HTTP 线程无锁遍历正在改变的 session deque。UI 直接显示 unknown/partial，不猜测状态。
6. 默认保存脱敏、限长回复摘要；调试原始数据需显式开关、脱敏、保留期限。source_revision、model_id、provider_profile_hash、prompt/schema 版本与运行参数必须可追踪。

**回归测试：** test_valid_first_reply_updates_current_request_summary；test_previous_repair_reply_does_not_leak_to_next_decision；test_all_receipts_reconcile_metrics；test_dashboard_concurrent_snapshot_is_consistent；test_secret_and_sensitive_text_redacted_before_disk_and_dashboard。

**验收：** 相同 action_id 在各介质中能追到底；没有 accepted 冒充 executed；计数可守恒且原始证据可追溯。

**失败/回滚策略：** 未知回执显示 unknown，不靠补造成功事件恢复指标。

**交付物：** 事件 schema、计数对账脚本、看板状态样本。

### D11 · 有界采集指标、录制背压与长期资源治理

**优先级：** P1。 **前置：** D02、D04。 **对应条目：** F16。

**目标：** 长时间运行不积累无界统计，也不让慢磁盘阻塞输入安全和最新画面发布。

**修改现有文件/目录：** `uga/capture/hub.py`；`uga/capture/ring_buffer.py`；`uga/recording/episode_writer.py`；`uga/recording/video.py`；`apps/agent/run.py`。
**建议新增：** `uga/core/metrics.py`。

**具体修改方式：**

1. 将 _gaps_ns 替换为有界窗口（建议 4096 项）用于滚动 P95，累计 max/count 使用 O(1) 更新；若需全程 P95 用固定内存直方图/分位数估计并标注误差，不能拿滚动 P95 冒充全程值。
2. 将 Frame 发布与视频编码/录制队列解耦；复用并加固 RecorderChannel，不在 CaptureHub 发布锁里做编码/磁盘 I/O。
3. 同时约束队列条目数与字节数，定义背压 deadline。训练录制模式禁止静默丢记录：队列耗尽进入可观察故障并安全停止或结束当前 Episode；观察模式的画面丢帧按明确策略计数。
4. EpisodeWriter 已有 buffer/row/byte 限制，应保留；为长会话增加分段 Episode 或增量 Parquet/JSONL 落盘，不能仅把上限调到无限。
5. RecorderChannel.close、视频 close、采集 worker 退出均提供有限关闭预算；处理队列满、worker 异常、disk full、写权限不足及半途 finalize 失败。
6. 日志轮转、限额和保留策略覆盖 supervisor.log；用户数据位置可配置。指标读取只复制小快照，不在全量排序期间占用采集锁。

**回归测试：** test_capture_stats_memory_is_bounded_after_large_frame_count；test_slow_recorder_does_not_block_latest_frame_publication；test_recording_overflow_is_explicit_and_not_silent_drop；test_recorder_close_has_deadline；test_disk_full_preserves_failure_evidence_and_neutralizes；确定性 100 万条统计流；真实 30 分钟/2 小时/8 小时分级资源测试。

**验收：** 固定配置下统计结构容量不随运行时长增长；每种背压路径都有指标和失败结论；资源测试报告 RSS、队列字节、句柄/线程数及日志体积。

**失败/回滚策略：** 暂不支持长录制时使用明确分段上限并安全结束，不回退为采集线程同步阻塞写盘。

**交付物：** 资源预算表、长稳测试报告、录制失败注入日志。

### D12 · 把游戏专属快规则隔离为可验证策略

**优先级：** P1。 **前置：** D03、D07、D05。 **对应条目：** F02、R20。

**目标：** 保留有效规则但消除按全局关键词、固定坐标和低风险标签无条件操作。

**修改现有文件/目录：** `uga/policy/grounded_vlm.py`；`uga/agent/session_state.py`；`uga/agent/closed_loop.py`；`uga/environment/profile.py`；`configs/games/mumu-xianyu.yaml`。
**建议新增：** `uga/environment/strategies/__init__.py`；`uga/environment/strategies/mumu_xianyu.py`。

**具体修改方式：**

1. 将 MuMu/仙遇任务追踪、关闭弹窗、境界、交易页面等规则移入显式 strategy；通用 Agent 不默认带商业游戏词表或协议自动同意行为。
2. 每条规则具备名称、profile 版本、页面前置谓词、目标 grounding、允许动作、risk、禁止条件、效果谓词、冷却和失效条件。
3. 校准热点必须受窗口 client 布局/比例/DPI 及页面身份约束；超出适用范围重新校准或 ABSTAIN。
4. “确定/提交/领取”先解释当前页面语境；账户、协议、支付、删除、发送等要求当前授权。实名/身份证页面维持人工处理，不把停止当需要恢复的卡住。
5. 协议勾选、tracker cooldown 和已点击状态从执行/效果回执更新，而不是提案时提前置位；重复失败触发有界恢复。
6. 识别升级目标时绑定具体对象/面板，而不是把页面任意最高等级当任务目标完成；强化同名控件的空间和页面作用域。

**回归测试：** 每条规则至少：1 正常例、1 相同词不同页面反例、1 低 OCR 例、1 几何变化例、1 未授权关键动作例。test_failed_agreement_proposal_does_not_mark_checkbox_clicked；test_confirmation_on_payment_page_is_not_generic_progress；test_generic_profile_never_runs_mumu_rules。

**验收：** 通用代码与游戏策略可分离测试；规则只能提出候选，无权跳过 D03；自动接受协议不再是隐含默认。

**失败/回滚策略：** 禁用具体 strategy 规则回到安全模型提案/观察，不将固定坐标散回主循环。

**交付物：** strategy 接口、规则注册表、逐规则 Fixture 语料。

### D13 · 统一看板语义、入口可移植性与运维控制

**优先级：** P2。 **前置：** D02、D08、D10。 **对应条目：** F06、R19。

**目标：** 用户能分清只读决策面板、命令面板和真实运行状态；本机控制边界不被部署方式误用。

**修改现有文件/目录：** `apps/agent/dashboard.py`；`apps/dashboard/__main__.py`；`uga/dashboard/server.py`；`uga/dashboard/controller.py`；`ui/src/dashboard.ts`；`scripts/run_mumu_autoplay.ps1`；`docs/guides/mumu-vlm-closed-loop.zh-CN.md`。

**具体修改方式：**

1. 保留两个入口但明确命名/用途；独立 uga-dashboard 当前未连接 runtime 时按钮禁用并显示 not connected，不伪装控制成功。
2. 若接入真实命令面板，使用线程安全 OperatorControl 适配器及统一 SafetyShutdown，命令不能直接操纵 InputBackend。
3. 保留 loopback、Host、Origin 和 token 校验；token 读取后从 URL 片段清除，缺 token 维持只读；响应不嵌入 token。除非明确需要远程多用户，不引入无关 OAuth 大改造。
4. 去除启动脚本用户专属 Python 默认路径、强制 .tooling 依赖；优先显式 -Python、项目 venv 和 PATH 解析，并输出受控诊断。
5. 区分 vision-mode 与 network-policy，云端模式显式提示截图出口；给 API 失败、采集失活、执行拒绝、人工门禁、预算耗尽提供不同状态。
6. 监督器日志轮转；自动重启遵循 D06。发布启动的无 anchor 降级必须有显式模式标签，不能当正式已验证发布。

**回归测试：** test_disconnected_dashboard_cannot_claim_command_success；test_command_adapter_uses_latched_shutdown；test_token_removed_from_fragment_without_losing_current_session_command；PS5.1/PS7、不同用户名/安装路径、只读安装目录的启动测试。

**验收：** 界面用词与事实一致；本机只读面板无需假装登录系统；任何操作入口都复用同一输入安全边界。

**失败/回滚策略：** 命令适配器未验证前维持只读，不放开绑定地址或认证校验。

**交付物：** 入口说明、运维故障排查表、界面截图/请求测试。

### D14 · 修复 Episode 到数据集的执行语义和防泄漏验收

**优先级：** P1。 **前置：** D04、D05、D07、D11。 **对应条目：** F12、G22。

**目标：** 用于训练的标签必须来自可验证执行，数据集划分和许可证保持可追溯。

**修改现有文件/目录：** `uga/recording/replay.py`；`uga/recording/schema.py`；`uga/dataset/processor.py`；`uga/dataset/manifest.py`；`uga/dataset/validator.py`；`uga/training/datasets.py`；`uga/training/motor_pipeline.py`。

**具体修改方式：**

1. 处理器优先使用 execution_receipt+execution_observation 关联；区分策略提案、实际执行、人类输入、rejected/expired/partial，不能把失败动作当正样本。
2. 保留原有单调时间与显式 ID 对齐约束；慢 VLM 问题通过 D04 新观察解决，不通过全局扩大 1 秒门槛掩盖旧状态。
3. Episode 写入、Replay 校验、DatasetManifest 校验共享新 schema 兼容规则；旧无 receipt 数据标 legacy/unqualified，显式迁移产生新版本而不是改历史 checksums。
4. 保留 episode/session/player/game 隔离及 locked Test-D；补充重复视频片段/同源切片哈希检测，确保同一实际会话换 ID 也不能悄悄跨 split。
5. 核对 manifest 声明的 duration/game_id/episode_id 与实物 metadata，质量评分与真实回执、视频、时间线一致；许可证缺失/限制不能只靠元数据字段默许。
6. 采集至少 5 小时正式语料属于单独受监督任务；用等待时间凑时长、不生成动作、复制同一片段、修改小时数字都不能满足门槛。

**回归测试：** test_rejected_and_partial_actions_excluded_from_positive_training_labels；test_fresh_execution_observation_round_trip；test_manifest_identity_duration_match_artifacts；test_cross_split_duplicate_episode_content_rejected；test_legacy_episode_cannot_satisfy_qualified_dataset_gate。

**验收：** 一条真实动作可从模型提案追到执行回执与对应画面；质量报告不把 proposal 计 executed；Test-D 锁定策略可重复检查。

**失败/回滚策略：** 维持旧数据只读兼容；新字段缺失时降级为 legacy 而非自动补造 receipt。

**交付物：** 数据 schema 迁移、数据质量报告、5 小时语料 manifest（实际完成后）。

### D15 · 补齐正式训练实现，不再用 smoke 替代五阶段训练

**优先级：** P1。 **前置：** D14。 **对应条目：** G22。

**目标：** 在保留确定性 CPU 基线的前提下，实现可执行、可评测、可审计的正式训练路径。

**修改现有文件/目录：** `uga/training/behavior_cloning.py`；`uga/training/motor_pipeline.py`；`uga/training/veomni.py`；`uga/training/dagger.py`；`uga/training/artifact.py`；`apps/training/__main__.py`；`uga/policy/fast_policy.py`。
**建议新增：** `uga/training/backends/`；`configs/training/qualified/`。

**具体修改方式：**

1. 保留 BehaviorCloningTrainer 作为 baseline/contract_test 并明确标签；它的全局多数 button_mask 不能冒充条件动作策略。
2. 实现受锁定依赖约束的 GPU 后端和设备探测，明确最小硬件/显存、样本格式、feature extractor 来源与许可证；没有 GPU 时标 NOT_RUN，不改报告 device 字段。
3. Motor 阶段训练条件化连续轴与多标签 buttons；训练、验证、测试指标分开，加入类别不平衡/无动作基线和校准，不能把训练集准确率当泛化。
4. Instruction 阶段用经审核任务-动作数据；Recovery 阶段用故障/恢复真实回执；Reasoning-gate 阶段学习何时调用慢模型与弃权；DAgger 阶段只聚合授权教师标签和安全运行产生的状态。
5. 每阶段必须有可运行 CLI/后端、训练日志、设备信息、config+dataset+checkpoint hash、指标阈值、许可证和上阶段关联。VeOmni 需要实际 launcher 实现才能声明支持分布式。
6. 每阶段先低成本 smoke，再正式体量；Test-D 只做最终评估，不参与调参。若数据不支持某阶段，保持该 stage blocked，而不是生成占位成功报告。

**回归测试：** test_conditional_button_predictions_depend_on_features；test_training_validation_test_metrics_are_separate；test_gpu_report_matches_detected_hardware；test_stage_artifacts_bind_previous_stage_and_dataset；test_test_d_never_enters_training_or_hyperparameter_selection。

**验收：** 五阶段均有真实执行证据才关闭 Models 门槛；统计量可由 checkpoint 和输入重算；没有正式证据时仍是 development candidate。

**失败/回滚策略：** 各 stage 独立版本；保留前一已验证 checkpoint，失败阶段不能晋升；安全门不随模型回滚削弱。

**交付物：** 五阶段训练 artifact、独立评估报告、设备与许可证清单。

### D16 · 补齐 Native/Win32 生命周期与故障注入

**优先级：** P1。 **前置：** D01、D02、D11。 **对应条目：** F01、G18、R21。

**目标：** 验证 Python-Rust 边界及真实 Windows 生命周期，不能用模拟 Frame 测试替代驱动/句柄安全。

**修改现有文件/目录：** `native/crates/uga-capture/src/ffi.rs`；`native/crates/uga-capture/src/windows/wgc.rs`；`native/crates/uga-capture/src/windows/dxgi.rs`；`native/crates/uga-capture/src/windows/mod.rs`；`uga/capture/`；`uga/windows/`；`tests/windows/`；`tests/unit/test_native_capture_security.py`。

**具体修改方式：**

1. 逐函数审查 FFI 所有权、create/next/release/destroy 配对、ABI 结构布局、data_len/stride/尺寸上限、错误路径、panic 边界、同句柄并发 capture/destroy。
2. 所有初始化/响应等待/关闭增加可观察 deadline；在驱动调用本身不可取消时，采用受控采集进程隔离和有限退出，不释放仍被线程使用的内存。
3. 保持加载前 SHA 与复合 WindowIdentity 校验。区分 hash 自洽与独立可信来源：读取同目录文件现算 hash 不能证明发布者身份。
4. 加入 device lost、HWND 复用、进程重启、DPI 变化、resize、alt-tab、最小化、显示器热插拔、双屏负坐标、UIPI 不兼容矩阵。
5. 自动测试使用自有 Fixture；真实物理输入只在当前明确授权且有操作者监督的 Windows 桌面执行。紧急中止设备/热键先测后开展长稳。

**回归测试：** test_native_abi_layout_and_error_release_paths；test_capture_destroy_concurrency_has_defined_behavior；test_stalled_native_worker_has_bounded_shutdown；test_recreated_hwnd_never_accepts_old_context；受监督 Win32 故障矩阵及 30 分钟 WGC/DXGI 多 API 矩阵。

**验收：** 所有 FFI 分配有对应释放证据；无旧 window context 输入；需要真实设备的门槛确实运行或明确 NOT_RUN；原生/输入故障不损害急停。

**失败/回滚策略：** 特定后端不可靠时标 unqualified 并显式禁用；不能无声降级然后宣称该后端通过。

**交付物：** Native 测试日志、句柄/内存趋势、Windows 硬件故障矩阵。

### D17 · 冻结运行时候选并进行闭环可靠性验收

**优先级：** P1。 **前置：** D01、D05、D06、D07、D08、D10、D11、D12、D13、D16。 **对应条目：** 全局治理/验收。

**目标：** 产出可信的运行时开发候选，而不是仅凭单次演示宣布完整 V1 完成。

**修改现有文件/目录：** `tests/`；`configs/benchmarks/`；`docs/runbooks/`；`docs/reviews/`。

**具体修改方式：**

1. 按固定语料执行 ≥200 条离线场景：正常、无文字图形、重复控件、加载、动画、弹窗、坐标变化、误导文本、敏感页面、错误输出与目标完成反例。
2. 执行 100 个受监督 Fixture/MuMu 任务（使用仓库既定分组，保持不同任务和重复次数可追溯），统计目标正确确认、无效动作、恢复、弃权、误点、模型调用量和费用。
3. 必须包含慢推理、429、断网、窗口销毁重建、focus theft、动作部分执行、急停、录制阻塞等系统级注入；测试真实 apps.agent 组合路径，不只测试孤立 SafetyShutdown。
4. 先 30 分钟，再 2 小时，再 8 小时长稳；每一级未达门槛不升级。报告墙钟时长、frame 年龄、capture P95/max、RSS 斜率、handles、线程数、队列和模型预算。
5. 安全门槛为零容忍：未授权关键操作、急停锁存后新提交、错误窗口输入、伪造 SUCCESS 任一发生均阻断候选。
6. 若启用 GLM-5.3-Flash 运行时配置，D09 另为前置；未启用时不强迫迁移模型。所有基准使用同一冻结 SHA 与版本化 profile。

**回归测试：** 测试矩阵见第 8 章；每个 Fxx 关联至少一个修复前失败、修复后通过的回归用例。完整 Python/UI/Rust 门槛和 clean install 不得遗漏。

**验收：** P0 清零；所有运行时 P1 已修复或明确关闭为非缺陷并给出测试；无未授权输入；生成不可变 candidate 证据包。成功率等效能阈值使用仓库既定资格标准；新增任务先冻结阈值后测，禁止事后按结果降低。

**失败/回滚策略：** 候选失败回滚到前一安全基线；保留失败证据及最小复现；不删除失败 Episode 来美化成功率。

**交付物：** runtime-candidate 报告、完整故障矩阵、200/100 语料清单与原始结果。

### D18 · 完成正式发布资格与干净安装/回滚

**优先级：** P1。 **前置：** D15、D17。 **对应条目：** G22。

**目标：** 只有同一冻结代码版本的真实证据齐备，才允许声明正式 V1。

**修改现有文件/目录：** `RELEASE_CHECKLIST.md`；`uga/release/`；`apps/qualification/__main__.py`；`scripts/build_release.ps1`；`scripts/run_bundle.ps1`；`docs/status/uga-v1-status.md`。

**具体修改方式：**

1. 沿仓库现有 qualification contract 运行 Capture、Control、Recorder、Dataset、Models、Generalization、Product、Governance 等门槛；不要另造一份宽松的总 PASS 覆盖旧门槛。
2. dataset 最低 5 小时、五阶段真实 GPU 训练、held-out Fixture D、UIPI、长稳、打包安装回滚缺一即保持 blocked；历史 d6da6d4/79742a4 报告仅作诊断。
3. 所有报告绑定完整 source_revision、clean tree、配置哈希、工具版本、原始日志和 artifact 哈希；源码变更后重跑受影响门槛。
4. 干净 Windows 账户/虚拟环境安装 wheel，不依赖源码目录、.tooling 或开发者缓存；执行所有声明入口和真实 bundle launcher。
5. 正式发布要求独立可信 manifest anchor/签名分发策略；开发无 anchor 模式清晰标 degraded，不得写 releasable:true。
6. 验证回滚到前一包后配置/数据 schema 兼容；删除/卸载只操作受拥有权验证的安装资源，不动用户游戏和原始 Episode。发布/上传仍需当前明确授权。

**回归测试：** preflight 零 blocker；manifest、bundle tree、安装、launcher、rollback 逐项执行。test_old_revision_evidence_cannot_promote_new_binary；test_missing_stage_or_external_anchor_blocks_qualified_release。

**验收：** 只有所有资格门槛实际通过才生成 releasable:true；开发候选与正式 V1 使用不同标记/发布说明。

**失败/回滚策略：** 保持上一已验证版本；任何证据冲突阻断发布，不修改 qualification 工具降低要求。

**交付物：** qualification ledger、preflight、release manifest、clean-install/rollback 报告。

### D19 · 整理开发交接与版本化结论

**优先级：** P2。 **前置：** D17。 **对应条目：** 全局治理/验收。

**目标：** 维护者能准确知道修了什么、还缺什么以及如何验证；运行时交付不冒充训练/正式发布完成。

**修改现有文件/目录：** `README.md`；`ARCHITECTURE.md`；`SECURITY.md`；`GLM53_HANDOFF_PLAN.md`；`docs/status/uga-v1-status.md`；`RELEASE_CHECKLIST.md`。

**具体修改方式：**

1. 逐项更新旧计划：done/partial/not-started/blocked，每项附实际 commit 与测试证据；删除与当前代码不符的默认路径和参数说明。
2. 在 README 区分 observe-only、supervised runtime candidate、qualified release；说明两个 Dashboard、vision-mode 与 network-policy、GLM 条件式接入。
3. 更新安全契约：统一 ActionGate、RunContext、Receipt、GoalEvidence、RecoveryBudget 和源数据到训练的关系图。
4. 输出最终变更文件清单、迁移指南、已知限制、不可自动操作范围和回滚说明。D18 未完成时正式 V1 状态维持未完成。
5. 交接给下一开发者时附本计划、bug ledger、coverage ledger、last verified SHA，不让下一模型凭提交说明重新推断通过状态。

**回归测试：** 文档命令在干净环境复核；参数名与 --help 自动比较。报告中的所有 PASS 均能找到对应日志、版本和 artifact。

**验收：** 没有无证据的“全部完成”“全项目无 bug”或“正式发布已合格”；未完成训练/硬件项明确保留。

**失败/回滚策略：** 文档可单独回滚，但不得恢复已被证伪的状态声明。

**交付物：** 最终交接包、迁移/回滚说明、逐缺陷关闭表。

---

## 8. 回归测试与故障注入矩阵

所有用例先在可重放 Mock/自有 Fixture 执行。下面列的是**待实施验收用例**，不是本次已经全部运行的测试。必须保存随机 seed、调度屏障、注入点和预期事件序列，禁止依赖不稳定 sleep 猜竞态。

| 编号 | 场景 | 注入方式 | 期望结果 | 任务 |
|---|---|---|---|---|
| T01 | 停在模型推理 await 期间 | 触发急停，再让模型返回合法 ACT | 锁存后 0 新 submit；迟到结果 discard | D02 |
| T02 | 监督器通过但尚未入队 | 在检查与 enqueue 之间触发 stop | 不得重授有效租约，不出现动作 | D02/D03 |
| T03 | FocusGuard 后、backend.submit 前 | 控制提交屏障与 trip 交错 | 以同一同步边界判定，trip 完成后无新 submit | D02/D03 |
| T04 | 规则与模型都输出 ui_back | 模型给任意坐标；规则给过期 profile 校准 | 两者不能凭 label 免检 | D03 |
| T05 | 框中心安全、偏移后进入禁区 | 执行 pointer_offset_y=-0.045 | 根据最终点拒绝 | D03 |
| T06 | 推理期间窗口销毁并复用 HWND | 生成新进程/窗口 generation | 旧 context 拒绝；不自动对新目标执行旧动作 | D03/D16 |
| T07 | 推理期间 DPI/窗口尺寸变化 | 几何代数推进 | 拒绝旧坐标，重新感知 | D03 |
| T08 | 提案接受但执行器拒绝 | 失焦/权限不足/租约过期 | rejected receipt，无 effect_pending，无成功标签 | D04 |
| T09 | 点击只完成 mouse_down | 下一 primitive 失败 | PARTIAL、释放输入、不记整体完成 | D04/D02 |
| T10 | 模型延迟 10 秒 | 新鲜执行观察与旧推理观察分别记录 | 数据按新观察关联，旧观察保留作因果追溯 | D04/D14 |
| T11 | 目标持续闪烁 | 稳定候选每帧被重置，超过 effect deadline | 按 deadline 退出 pending | D05 |
| T12 | 仅红/绿通道变化 | 蓝通道保持不变 | 多通道差异被检测；alpha 变化不伪造效果 | D05 |
| T13 | 无 required_evidence、两次 DONE | 两帧空 OCR 且高 confidence | UNVERIFIED 而非 SUCCESS | D05 |
| T14 | 两次完成证据来自不同 task/window | 切任务/切窗口 | 不得拼接完成证据 | D05/D07 |
| T15 | 恢复预算为 0/1/2 | 顺序触发高清、返回和重定位 | 所有恢复遵守同一总预算 | D06 |
| T16 | continuous+安全 BLOCKED | 多次工厂重建/进程重启 | 安全状态不自动清除 | D06 |
| T17 | 主线 10 级变 20 级 | 带轻微 OCR 抖动 | 关键目标改变推进 generation，非关键抖动不推进 | D07 |
| T18 | 主/验证器返回 429 | 带有效/无效 Retry-After | 有界退避，不传播为未分类崩溃 | D08 |
| T19 | 401、配额耗尽、错误 provider | 重复请求监督器开启 | 快速停止、清晰原因、无无限烧请求 | D08/D13 |
| T20 | 恶意或畸形模型 JSON | bool/string/NaN/Inf、额外字段、混合坐标 | 本地严格拒绝，不产生动作 | D08 |
| T21 | schema 不支持再修复 | 每次请求接近 timeout | 总 deadline 与全局 attempt budget 约束整次决策 | D08 |
| T22 | GLM53 + --vlm-no-thinking | 不调用真实 API 的请求构造检查 | 启动时拒绝参数组合 | D09 |
| T23 | 正常回复紧随修复回复 | 两个不同 request_id | 当前日志只显示当前 reply | D10 |
| T24 | 录制磁盘缓慢/已满 | 队列达到容量与字节限制 | 最新帧/急停不被磁盘阻塞，录制明确失败 | D11 |
| T25 | 30 分钟→2 小时→8 小时运行 | 持续刷新看板和录制 | 统计/队列有界，RSS 与 handles 无持续失控增长 | D11/D17 |
| T26 | 同名确定出现在不同语境 | 普通奖励/协议/支付/删除页面 | 按页面授权与风险策略分别处置 | D12 |
| T27 | state 写一半/多进程/只读目录 | 中断 replace 之前；并发同 namespace | 恢复安全、错误可见、无旧状态授权 | D07 |
| T28 | 同会话切片换 ID 跨 split | 相同来源/重复内容不同 episode ID | 数据泄漏检测拒绝 | D14 |
| T29 | 正式模型报告只有 CPU smoke | 缺真实 GPU 日志、stage 输出 | qualification blocked 而非 PASS | D15/D18 |
| T30 | 旧 SHA 证据+新 wheel | 改一个安全关键模块后不重测 | 不能晋升 qualified release | D18 |

### 8.1 覆盖与指标要求

安全负例必须覆盖每一种动作来源和两条真实入口。每个修复至少有“修复前能稳定失败、修复后通过”的测试；本交接包的隔离检查应转成导入真实仓库模块的 pytest，不能把复制源码的测试当最终回归。

关键指标要写清粒度、时间来源和分母：动作按逻辑/物理原语分别计数；时间使用单调纳秒，墙钟只用于展示；frame 新鲜度用采集时间，不用日志写入时间；结果分 SUCCESS/FAILURE/BLOCKED/ABORTED/UNVERIFIED，不把进程 exit 0 当任务成功。

`scheduled = executed + rejected + expired + flushed + pending` 必须在同一计数粒度下成立；PARTIAL 由其原语回执推导，不能在等式中重复计数。视频/日志缺失时报告 incomplete，不擅自补造事件。

### 8.2 已执行的 8 项隔离检查

| 检查 | 本次隔离结果 | 对应问题 | 仍需补做 |
|---|---|---|---|
| EX01 | 红/绿通道改变而抽样摘要不变 | F07 | 导入真实 Frame/region_digest 及不同 stride/format 测试 |
| EX02 | 空证据、间隔足够的两次高置信候选被确认 | F09 | 完整 GoalVerifier/监督器/CLI 默认路径 |
| EX03 | max_recoveries=0 仍产生 BACK 指令 | F05 | 真实预算状态机与物理恢复回执 |
| EX04 | 无裁剪时高清重试编码参数不变 | F14 | 实际 PNG 尺寸与模型输入记录 |
| EX05 | 验证器谓词接受 bool/字符串/Infinity/超过 1 值 | F11 | 完整 HTTP 响应解析及 strict Schema |
| EX06 | 框中心不在禁区，但偏移后的实际点进入禁区 | F03 | 完整 ActionGate 至 InputExecutor 路径 |
| EX07 | 混合坐标被整体缩放成合法框 | F11 | provider 适配器与实际 parser |
| EX08 | 持续像素变化分支在总期限后仍提前返回 pending | F04 | 完整 observe 状态机与真实时钟 Mock |

本次结果 **8/8 重现的是旧分支行为**。这八项并非全部等价的端到端“bug 复现”，尤其 EX06 是几何谓词、EX08 是选定提前返回分支。脚本和 JSON 明确记录这些边界。

---

## 9. 数据、训练、发布的独立完成条件

### 9.1 五阶段训练交付矩阵

| 阶段 | 输入 | 需要实现的输出 | 不接受的替代品 |
|---|---|---|---|
| Motor BC | 已执行且对齐的新鲜观察—动作对 | 条件化轴/按钮模型、独立验证指标、checkpoint | 固定多数 button mask 冒充条件策略 |
| Instruction | 有任务语义和授权范围的经审查样本 | 任务条件策略与指令遵循评估 | 只改 prompt 然后写“已训练” |
| Recovery | 真实故障与有界恢复回执 | 恢复策略、失败/退出分类指标 | 重试 while True 或伪造成功 Episode |
| Reasoning gate | 不确定性、风险、代价和结果数据 | 慢模型调用/弃权策略与误触发指标 | 永远调用或永远不开模型冒充学习门控 |
| DAgger | 受监督运行中的分布偏移状态和教师标签 | 聚合数据版本、新 checkpoint、前后对照 | 复制旧数据、无监督游戏乱试或把测试集加入训练 |

当前真实训练缺口来自项目状态文档与代码，不应通过“把 Protocol 命名为 Backend”关闭。模型文件完整性、许可证和来源哈希继续沿用现有 artifact 设计，正式训练是增量实现。[S21] [S22] [S31] [S32]

### 9.2 V1 范围不要扩大或偷换

架构文档对 V1 的泛化范围限定为开发者拥有的 Fixture 场景 Train A/B/C 和 held-out 场景 D。不能把几个 MuMu 操作演示宣传为任意商业游戏泛化，也不能把 held-out D 回灌训练来提高报告分数。[S01]

发布以仓库既有 qualification 工具和契约为准；本计划的任务编号不是替代发布门槛。构建 wheel 成功、仓库有 MIT、单元测试全绿，分别只是局部门槛，不等于正式发布批准。[S32]

### 9.3 证据最小组成

每条资格证据应包含冻结 SHA、源码清洁状态、profile/config/prompt/model/data 哈希、测试环境、开始结束单调/墙钟时间、原始结果文件、聚合指标、失败样本、skip 原因和生成工具版本。构建及测试结果不得在失败后手工编辑成通过。

无 GPU/双屏/UIPI 环境属于未完成测试，不是修改硬件门槛的理由。无需阻塞前面不依赖这些设备的安全修复，但正式 V1 门槛必须保留 blocked。

---

## 10. GLM执行协议与逐PR完成定义

### 10.1 给GLM的直接执行指令

> 你是本项目的开发执行者，不是已有资格报告的背书者。首先读取本计划、audit coverage、bug ledger 和仓库当前 HEAD。以 34eba5ebe141862d590462c4551dbe0a2514d51a 为审计基线；若 HEAD 不同，先列差异并重查受影响任务。
>
> 按 D00—D19 的依赖执行；先 P0，后功能。每次只处理一个逻辑任务或可独立审阅的小批次。开始修改前报告“失败复现、将改文件、不能破坏的不变量、验收命令”。先写导入真实模块的失败测试，再进行最小修复，再跑局部和整合门槛。
>
> 不得删除负例、扩大时效/降低置信度、给特殊标签免检、无条件恢复 BLOCKED、伪造 receipt/goal evidence、把旧 generation 重写成新 generation、手改 qualification 结果。不得把网络 API 的 reasoning_content 直接当动作。
>
> 无当前明确授权，不得操作真实游戏、同意协议、提交个人资料、支付、发布/推送或调用付费 API。历史文档中的授权仅是历史记录。缺硬件时推进 Mock 与代码工作，把硬件项标 NOT_RUN。
>
> 每批交付：变更文件清单、原失败测试及修后结果、完整命令和日志路径、行为/Schema 变化、未完成项、风险、回滚方式、准确 commit。没有证据就不能标 done。运行时开发候选与正式 V1 分开汇报。

### 10.2 每个PR的完成定义

PR 描述必须回答六个问题：修复了哪个 F/G/R/C；原来为何错；现在在哪一层阻断；新增哪个真实模块回归；影响哪些 schema/数据/旧配置；什么证据说明没削弱其他安全边界。

安全/并发/Native/训练数据变更要有独立审阅或至少明确的第二轮审查记录。由同一个模型自称“已 review”不等于独立验证；测试和原始证据才是主要依据。

任务状态流：`not_started → reproducing → fixing → tests_passed → reviewed → verified_closed`。环境不足用 `blocked_environment`；未获得实际操作许可用 `blocked_authorization`；调查后非缺陷用 `not_a_bug` 并保留理由及测试。不能把 blocked 计入 done。

### 10.3 禁止的“快速修复”

| 错误做法 | 正确替代 |
|---|---|
| 让 ui_back/ui_close 跳过检查 | 保留统一硬安全门，提供可信校准/页面证据 |
| 放大动作 TTL 补偿慢模型 | 丢弃旧 context，取得执行前新鲜观察 |
| 把任何画面变化当成功 | 定义目标/效果谓词与对应证据 |
| 每次卡住重建监督器清零 | run 级预算与终止原因白名单 |
| 429 直接让进程重启 | 有界重试与全局请求预算 |
| 把 pytest 失败/Native 缺失改 skip | 修断言或建立真实作业前置 |
| 用 CPU smoke 填写 GPU 训练报告 | 实际训练或明确 NOT_RUN |
| 写一个“总资格 PASS”JSON | 由现有 preflight 验证真实原始证据 |

---

## 11. 建议验证命令与交付目录

下面命令是开发执行指南，本次未在完整仓库运行。使用干净开发工作区；环境安装命令不得覆盖用户已有全局环境。

### 11.1 基线与不触发真实输入的测试

```powershell
git rev-parse HEAD
git status --porcelain
python --version
node --version
cargo --version

python -m pip install --require-hashes -r requirements-lock.txt
python -m pip install --no-deps --no-build-isolation -e .

python -m ruff check .
python -m mypy uga apps

New-Item -ItemType Directory -Force build\audit | Out-Null
python -m pytest tests/unit tests/contracts tests/integration -q -ra `
  --junitxml=build/audit/python-nonphysical.xml

npm ci --include=dev
npm run typecheck
npm run build

Push-Location native
cargo fmt --all --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cargo build -p uga-capture --locked
Pop-Location
```

新增具体测试名称以任务卡为准，实施后再生成精准执行命令；不要使用仓库没有注册的 pytest marker 来假装排除物理测试。`tests/windows` 逐项按实际前置选择，启用任何物理输入测试前先取得监督授权并验证急停。

构建本地 DLL 后，Native 集成作业应显式设置其路径和期望 SHA；本地构建的 digest 用于一致性校验，不等于外部分发的可信发布者身份。发布场景仍需独立 anchor。

### 11.2 推荐开发交付目录（将来在仓库生成）

```text
docs/reviews/
  audit-coverage-2026-09-16.md
  bug-ledger-2026-09-16.md
  runtime-candidate-<commit>.md
build/audit/<commit>/
  environment.json
  baseline/
  regression/
  fault-injection/
  resource-soak/
  dataset/
  models/
  qualification/
  checksums.json
```

每个目录里保存原始日志和可重新计算的输入，不只保存截图或一句总结。正式证据包是否保存在 Git 仓库、GitHub artifact 或外部存储，遵循现有 qualification 契约；大体积视频/模型不要擅自提交到源码仓库。

---

## 12. 审查覆盖清单与剩余边界

本表刻意区分深入的主链路和仍需补齐的部分，不能把目录清单浏览说成逐行审完。

| 代码面 | 本次已核对 | 仍须 D00 或相应任务补齐 |
|---|---|---|
| 架构/状态/旧计划 | 架构、安全、发布清单、旧审计与交接、当前 CI | 各契约文档逐项与最新修复 SHA 对齐 |
| CLI/真实组合 | apps.agent.run 关键配置、资源启动、stop、record、loop 组合 | 所有其他 apps 入口、所有异常路径全覆盖 |
| core/闭环 | 推理→freshness→assess→提交、continuous、效果/目标/恢复关键方法 | 全部辅助函数和所有分支的集成覆盖 |
| control/safety | lease/arbiter/scheduler/executor/FocusGuard/Shutdown/Watchdog 实现 | SendInput/gamepad 底层、OS 竞态和硬件实测 |
| perception/policy | schema、模型传输、grounded 规划/解析/规则/验证器 | 全部 OCR 适配器、图像编码格式与性能、FastPolicy 各后端 |
| session/rules | 任务记忆、持久化、主线/部分游戏规则 | 所有固定区域/规则的真实画面反例、其他 profile |
| capture | Hub、RingBuffer、背压和统计路径 | WGC/DXGI/GDI 全部错误路径与具体机器性能 |
| native | FFI 主要 create/capture/release/destroy 生命周期及源码树 | Windows 后端逐函数审查、ABI/驱动/双屏/UIPI 矩阵 |
| recording | EpisodeWriter、RecorderChannel 关键实现 | Replay/Video 完整异常矩阵、增量写入/关闭兼容性 |
| dataset | Processor、Manifest/split 隔离和 artifact 校验入口 | Validator/OpenCUA/所有导入导出、manifest 与实物全面对照 |
| training | BehaviorCloningTrainer 全文、VeOmni 接口、状态文档 | motor pipeline/artifact/DAgger/所有 recipes 逐函数与真实训练 |
| benchmark/release | 架构范围、正式门槛、launcher 测试与 CI | 全部资格工具代码及当前 SHA 真实证据 |
| UI/dashboard | 通用认证/前端 token 流程、只读运行面板、命令连接状态 | 全部 Replay/Dataset 前端、交互与浏览器并发回归 |
| 依赖/供应链 | pyproject、CI 固定引用与现有哈希机制 | 锁文件逐项许可证/漏洞/来源核查、release 独立可信 anchor |

在这些未完成项被补齐之前，不应出具“全项目无其他 bug”的结论。

### 12.1 来源索引

所有文件引用均固定到审计 SHA `34eba5ebe141862d590462c4551dbe0a2514d51a`（`https://github.com/chenfengyimei/VLGameAgent/blob/34eba5ebe141862d590462c4551dbe0a2514d51a/<path>`）；CI 证据为 Actions run 35062922806 的 Python 作业日志；模型官方文档为 2026-09-16 访问时的 `https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash`，运行时接入实施前需再次确认参数。编号对照：

[S01] ARCHITECTURE.md；[S02] uga/core/agent_loop.py；[S03] apps/agent/run.py；[S04] uga/agent/closed_loop.py；[S05] uga/safety/shutdown.py；[S06] uga/safety/watchdog.py；[S07] uga/control/executor.py；[S08] uga/control/scheduler.py；[S09] uga/control/lease_manager.py；[S10] uga/control/arbiter.py；[S11] uga/safety/focus_guard.py；[S12] uga/policy/grounded_vlm.py；[S13] uga/policy/vlm_planner.py；[S14] uga/perception/schema.py；[S15] uga/agent/session_state.py；[S16] uga/capture/hub.py；[S17] uga/capture/ring_buffer.py；[S18] uga/recording/episode_writer.py；[S19] uga/dataset/processor.py；[S20] uga/dataset/manifest.py；[S21] uga/training/behavior_cloning.py；[S22] uga/training/veomni.py；[S23] uga/dashboard/server.py；[S24] apps/dashboard/__main__.py；[S25] ui/src/dashboard.ts；[S26] apps/agent/dashboard.py；[S27] scripts/run_mumu_autoplay.ps1；[S28] tests/windows/test_release_bundle.py；[S29] .github/workflows/ci.yml；[S30] native/crates/uga-capture/src/ffi.rs；[S31] docs/status/uga-v1-status.md；[S32] RELEASE_CHECKLIST.md；[S33] GLM53_HANDOFF_PLAN.md；[S34] docs/reviews/post-glm-implementation-audit.md；[S35] docs/guides/mumu-vlm-closed-loop.zh-CN.md；[S36] pyproject.toml；[S37] SECURITY.md；[S38] tests/integration/test_dashboard_server.py；[S39] Actions run 35062922806；[S40] GLM-5.3-Flash 官方模型文档。

---

## 附录A：首批范围、关键落地约束与验收目标

### A.1 首批变更

**首批只做 D01、D02、D03：恢复可信 CI、接入锁存急停、取消特殊标签的安全绕过。** 这三项稳定后，再做执行回执（D04）和效果验证（D05）。继续添加更多点击规则，应排在这些基础边界修好之后。

合并顺序：

```text
M0：D00、D01
M1：D02、D03
M2：D04、D05、D06、D07、D12
M3：D08；选择迁移运行时模型时执行 D09
M4：D10、D11、D13、D16
M5：D14、D15
M6：D17、D18、D19
```

CI、传输 Mock、停机测试可以适当并行。但 `closed_loop.py` 与 `agent_loop.py` 的改造应该串行小步合并，避免多个执行者各自重写整文件，最后无法确认哪条安全路径被覆盖。

### A.2 五项关键落地约束

1. **急停不能只靠取消推理任务。** 正确策略是先失权，再结束正常任务。模型可以迟到，但迟到结果必须因为运行代数和锁存状态而失去执行资格。日志、磁盘、HTTP 服务关闭和模型请求返回，都不能排在输入失权之前。验收同步边界：从输入网关确认急停锁存完成起，不得再出现新的 `submit`。已经被操作系统接收的历史输入，不应被描述为还能撤回。
2. **效果超时必须是独立的硬期限。** 每个动作至少区分：实际执行时间、最短效果观察时间、稳定候选窗口、绝对效果截止时间。持续闪烁可以重置“稳定候选窗口”，但不能延长“绝对效果截止时间”。无论走像素、OCR、锚点还是页面状态分支，到期都必须退出 pending。
3. **恢复预算必须跨监督器生命周期。** 预算由组合根持有 run 级预算，并分别记录动作级、状态级和运行级计数。不能把卡住后的 BACK 改名成“普通返回”来绕过恢复预算。
4. **数据问题不能通过放宽时间限制掩盖。** 慢模型导致观察过旧时，保留两份关联：`inference_observation_id`（模型当时看到了什么）与 `execution_observation_id`（真正执行前验证了什么）。训练标签依靠后者与实际执行时间对齐。不能把旧观察的时间戳改成当前时间，也不能把一秒限制统一放大到几十秒，就宣称数据已经正确。
5. **游戏规则只能提出候选，不能直接拥有权限。** 规则模块应包含适用 profile 与版本、页面前置条件、目标识别依据、候选动作、风险类别、禁止条件、冷却条件、效果谓词。“确定”在普通奖励页面、支付页面、协议页面中不是同一种操作。规则必须先判断语境，再交给统一安全门。

### A.3 验收标准（拟定要求，不是已达到结果）

| 验收面 | 必须满足 |
|---|---|
| 急停 | 锁存完成后新输入提交为 0；慢模型和迟到结果不能重新启动输入。 |
| 授权 | 特殊标签、规则、恢复均不能绕过安全门；实际落点不能进入禁区。 |
| 上下文 | 旧窗口、旧几何、旧任务、旧运行代数的动作全部拒绝。 |
| 执行事实 | `accepted` 不冒充 `executed`；部分点击不能算完整成功。 |
| 效果验证 | 持续动画、OCR 抖动也不能让 pending 无限延续。 |
| 恢复 | 0/1/2 配置语义明确；重建监督器不清空运行级预算。 |
| 成功判定 | 没有目标证据不能写 SUCCESS；不同任务/窗口证据不能拼接。 |
| 模型调用 | 429 有界退避；认证/配额错误不会无限重试；不合法 JSON 不产生输入。 |
| 数据 | 训练动作关联实际执行回执和新鲜观察；失败提案不作为正标签。 |
| 长稳 | 指标、队列、日志有界；慢磁盘不阻塞急停和最新帧发布。 |
| 发布 | 当前代码必须对应当前证据；硬件或训练未运行则保持未完成。 |

急停性能目标：在受监督 Fixture 中测量热键到输入失权、再到释放完成的延迟，初始目标 **P99 ≤ 100 ms（失权）、释放完成 ≤ 200 ms**。需要实际测量最差情况，不能只跑一次正常路径。

长稳按 **30 分钟 → 2 小时 → 8 小时** 逐级进行。每级都记录内存、句柄、线程、队列、采集间隔、帧年龄和模型调用预算；上一级失败就先修复，而不是继续延长运行。

### A.4 GLM-5.3-Flash 专项（仅当选择运行时迁移时）

- 官方 model code `glm-5.3-flash`，`thinking.type` 仅支持 `enabled`；现有脚本固定 `--vlm-no-thinking`，因此启动前参数兼容性检查必须拒绝该组合，不能默默发 disabled。
- 现有客户端在 `content` 为空时会尝试使用 `reasoning_content`。**不要把推理文本作为可执行动作正文的备用来源；正文为空或未完成时应弃权或报模型错误。**
- 真实 API 兼容测试应单独获得调用授权并设置额度；未获授权时相关项标 NOT_RUN。
