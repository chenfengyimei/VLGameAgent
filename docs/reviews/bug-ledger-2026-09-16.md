# VLGameAgent 缺陷、风险与资格缺口台账（2026-09-16）

- **基线 SHA：** `34eba5ebe141862d590462c4551dbe0a2514d51a`（冻结时等于 HEAD，工作区干净）
- **CI 事实：** Actions run `35062922806` = **failure**（Python 作业：1 failed / 504 passed / 8 skipped，共 513 项）
- **完整计划：** `docs/reviews/glm53-flash-execution-plan-2026-09-16.md`
- **证据级别：** C=CI 运行/日志事实；S=源码控制流可确认；I=隔离检查（片段/分支重放，非完整应用）；R=有依据的风险，需故障注入/实机验证；D=官方模型文档对照。
- **优先级：** P0=开始真实自动操作前必须关闭；P1=闭环正确性/证据/可靠性/资格；P2=诊断/性能/运维。
- **状态流：** `open → reproducing → fixing → tests_passed → reviewed → verified_closed`；另有 `blocked_environment` / `blocked_authorization` / `not_a_bug`（须保留理由与测试）。blocked 不计入 done。
- **纪律：** 未复现风险保持 R，禁止改写为“已证明漏洞”；每条关闭必须挂“修复前失败、修复后通过”的回归测试与准确 commit。

## 汇总表

| 编号 | 优先级 | 证据 | 状态 | 问题 | 主符号/文件 | 修复任务 |
|---|---|---|---|---|---|---|
| F01 | P0 | S | tests_passed(local) | 真实入口急停未锁存输入失权 | `apps/agent/run.py::request_stop`；`uga/core/agent_loop.py::step`；`uga/safety/shutdown.py::SafetyShutdown`、`uga/safety/watchdog.py::RuntimeWatchdog` 已接入 | D02、D03、D16 |
| F02 | P0 | S | tests_passed(local) | ui_back/ui_close/ui_promote 标签被当信任依据 | `uga/agent/closed_loop.py::assess`（保留标签分支要求可信来源）；`uga/core/agent_loop.py`（跳过分支已删除）；`uga/policy/grounded_vlm.py::last_decision_source`（运行时赋值公开属性）；`uga/safety/action_gate.py` | D03、D12 |
| F03 | P1 | S+I | tests_passed(local) | 禁点校验点(框中心)≠最终点击点(含偏移) | `uga/safety/action_gate.py::resolved_click_point`；`uga/agent/closed_loop.py::ActionValidator`（center+最终落点双查） | D03 |
| F04 | P1 | S+I | open | 效果验证分支提前 return，pending 可能永不超时 | `uga/agent/closed_loop.py::ClosedLoopSupervisor.observe`（像素稳定候选/锚点分支在总超时判断前返回） | D05 |
| F05 | P1 | S+I | open | 恢复预算不覆盖 BACK；max_recoveries=0 仍可 BACK | `uga/agent/closed_loop.py::ClosedLoopSupervisor._request_recovery` | D06 |
| F06 | P1 | S | open | continuous 对终止状态统一重建监督器，计数清零 | `uga/core/agent_loop.py`（continuous factory 重建）；`scripts/run_mumu_autoplay.ps1`（非零退出重启） | D02、D06、D13 |
| F07 | P1 | S+I | open | region_digest `[::16]` 抽样对部分颜色通道失明 | `uga/agent/closed_loop.py::region_digest` | D05 |
| F08 | P1 | S | open | 任务记忆 generation 与主循环 _task_generation 未统一 | `uga/agent/session_state.py::GameSessionState`；`uga/core/agent_loop.py::_task_generation` | D07 |
| F09 | P1 | S+I | open | 无 required_evidence 时两次高置信 DONE 即判成功 | `uga/agent/closed_loop.py::GoalVerifier._consider_snapshot` | D05 |
| F10 | P1 | S | open | VisionRateLimitedError 未被新闭环统一捕获（只捕 BackendUnavailableError） | `uga/policy/vlm_planner.py`（抛出方）；`uga/core/agent_loop.py`、`uga/policy/grounded_vlm.py`（捕获方） | D08 |
| F11 | P1 | S+I | open | 本地结构化校验比声明 Schema 宽松（float() 强转、混合坐标整体除 1000、confidence 无上界/类型检查） | `uga/policy/grounded_vlm.py`（解析/验证谓词）；`uga/perception/schema.py` | D08、D09 |
| F12 | P1 | S | open | 排队被当真实动作；观察关联过旧（推理前 observation_id；数据侧默认 1s） | `uga/core/agent_loop.py`（arbiter accepted→start_action）；`uga/control/scheduler.py`、`uga/control/executor.py`、`uga/dataset/processor.py` | D04、D10、D14 |
| F13 | P2 | S | open | 正常成功回复未更新 last_raw_reply，看板摘要空/陈旧 | `uga/policy/grounded_vlm.py::GroundedVlmPlanner.decide` | D10 |
| F14 | P2 | S+I | open | 高分辨率恢复未真正提高有效分辨率（仅改裁剪 padding） | `uga/policy/grounded_vlm.py::_images`（high_resolution_retry） | D05、D09 |
| F15 | P1 | S/R | open | 会话持久化无作用域/原子替换/恢复后证据门槛 | `uga/agent/session_state.py`（持久化）；`apps/agent/run.py`（共享 session_state.json） | D07 |
| F16 | P2 | S/R | open | CaptureHub 间隔统计无限增长全量排序；发布锁内同步录制 | `uga/capture/hub.py`（_gaps_ns、stats、_publish）；`uga/recording/episode_writer.py`（有界，勿误伤） | D11 |
| F17 | P1 | C | tests_passed(local) | CI DLL 路径短名/长名字符串比较误报（RUNNER~1 vs runneradmin） | `tests/windows/test_release_bundle.py::test_clean_bundle_launches_agent_with_pinned_native_library` | D01 |
| G18 | P1 | C/S | tests_passed(local) | Python 作业缺 DLL，5 项 Native 测试静默 skip | `.github/workflows/ci.yml`（新增 native-python-integration 作业） | D01、D16 |
| R19 | P1 | R | open | 模型网络出口/秘密/隐私边界不显式 | `uga/policy/vlm_planner.py`（urllib 默认行为、错误正文入异常）；截图/OCR/日志 | D08、D09、D10、D13 |
| R20 | P1 | R | open | “确定/提交”类 OCR 快规则缺页面语境授权；协议勾选提案期置位 | `uga/policy/grounded_vlm.py`（快规则）；`uga/agent/session_state.py` | D03、D12 |
| R21 | P1 | R | open | FFI init/响应/关闭缺外层 deadline（通道等待/线程 join） | `native/crates/uga-capture/src/ffi.rs`；`uga/capture/native_adapter.py` | D16 |
| G22 | P1 | S | open | 正式训练/发布能力与证据未完成（CPU smoke≠五阶段真实训练） | `uga/training/behavior_cloning.py`、`uga/training/veomni.py`；`docs/status/uga-v1-status.md` | D14、D15、D18 |
| C23 | P1 | D/S | open | GLM-5.3-Flash thinking.type 仅支持 enabled，与 --vlm-no-thinking 不兼容 | `scripts/run_mumu_autoplay.ps1`、`apps/agent/run.py`、`uga/policy/{grounded_vlm,vlm_planner}.py` | D09 |

## 条目明细

### F01 真实运行入口未接入锁存急停和独立看门狗（P0 · S · tests_passed(local)）
- **触发条件：** 推理/验证进行中按急停或到时长；停止循环与未结束 step 交错。
- **复现入口：** T01/T02/T03 已实现为回归测试（tests/integration/test_latched_shutdown.py）。
- **预期行为：** 急停回调先 `SafetyShutdown.trip()`（禁用→撤销租约→清队列→释放输入，锁存幂等），再通知 asyncio stop；锁存确认后新 submit=0；迟到推理结果因 run_generation/锁存失权。
- **修复提交：** `871cfc4`（2026-09-16，未推送）。落地内容：(1) run.py 组合根构建 SafetyShutdown+RunContext+RuntimeWatchdog，急停/信号/超时/崩溃全部先 trip 后通知，打印排在失权之后；AgentEnableState 初始 False，急停热键与看门狗 monitor 就绪后才显式 arm；(2) 新增 uga/core/run_context.py（run_id、单调 generation、cancel 锁存，stop/generation 推进均使在途 stamp 失效，禁止改写 generation 续命）；(3) agent_loop 在每次 to_thread 推理/评估返回后、EXECUTE/RECOVER 授权前复核 stamp，迟到结果只记 discarded_stale 事件+计数，不授权不入队；(4) 看门狗心跳挂在 scheduler 真实 tick 上（控制面活性，非盲定时器），monitor 线程独立巡检；(5) continuous 重建不得清除安全锁存（should_stop 时停止而非重建），且重建推进 run generation 使旧结果失效；(6) --watchdog-timeout-seconds CLI（默认 60s，>0 校验）。
- **关闭证据：** 11 项新测试全绿（停机中推理迟到不提交、assess→入队间隙停止、fast policy 迟到丢弃、锁存后新 execute 被 AGENT_DISABLED 拒绝且不产生新提交、continuous 不清锁存、心跳随真实 tick、RunContext 代数/取消/锁存语义、看门狗活体）；全套件 525 passed + 1 opt-in skip + 103 subtests；ruff/mypy(177) 绿。**受监督 Fixture 实测热键→失权 P99≤100ms/释放≤200ms 尚未实测（NOT_RUN，需授权后按 D17 流程测）。**

### F02 ui_back/ui_close/ui_promote 字符串被当成信任依据（P0 · S · tests_passed(local)）
- **触发条件：** 模型返回保留标签但任意坐标，或推理后窗口/几何已改变。
- **复现入口：** T04/T05/T06/T07 已实现为回归测试（test_closed_loop_supervisor.py 新增 4 项 + test_agent_loop.py 既有路由测试保持绿）。
- **预期行为：** 删除按标签放行与 validate_execution_frame 跳过；可信 DecisionSource 由运行时代码赋值；所有来源过统一安全门（校准热点可豁免 OCR 文字 grounding，不豁免运行状态/窗口身份/任务代数/几何/最终落点/禁点/期限）。
- **修复提交：** `01e9fe9`（2026-09-16，未推送）。落地内容：(1) 新增 `uga/safety/action_gate.py` 统一门原语：`is_trusted_deterministic_source`（仅 ocr_* 规则来源可信，模型 JSON 自报无权限）、`resolved_click_point`（最终落点）、`point_is_clickable`（禁点+诱饵）、`generations_consistent`（请求/窗口/几何/任务代数一致性，单一实现供所有来源复用）。(2) assess() 保留标签分支现在要求可信来源 + 生成守卫 + 最终落点门，任一失败 REOBSERVE/BLOCK；模型自报 ui_back/ui_close 落入 back-intent 路由→校准热点（模型自己的 box 永不执行）；ui_promote 无可信来源即走正常验证。(3) 主循环删除 validate_execution_frame 跳过分支：location_calibrated 出口改为共享 `validate_execution_context`（窗口身份/几何仍强制，仅豁免像素动画）；RECOVER 恢复点击同样过执行上下文守卫，窗口重建后不执行。(4) to_recovery_gui_action 校准热点过禁点门，不通过则回退键绑定，无键则 fail-closed。(5) GroundedVlmPlanner 暴露 `last_decision_source` 属性（此前 agent_loop getattr 读的是不存在的公开名，永远 None——顺手修复），agent_loop 将其传入 assess。(6) _is_back_intent 增加 ui_back/ui_close 标签集合（无特权来源时路由）。
- **关闭证据：** 4 项新安全负例全绿：模型自报 ui_back 的原始 box 被丢弃且点击重锚到校准热点、可信来源出口落点进禁区被拒（REOBSERVE）、可信出口在窗口重建（generation 变化）后 REOBSERVE、框中心安全但偏移后落点进禁区被拒（EX06 回归）；全套件 529 passed + 1 opt-in skip + 103 subtests；ruff/mypy(178) 绿。注意：schema 校验 pointer_offset ∈ [-0.25,0.25]，EX06 的"任意偏移进禁区"被限制在合法偏移范围内复现。

### F03 禁点校验点与最终点击点不同（P1 · S+I · EX06 · tests_passed(local)）
- **触发条件：** 目标框配置非零 pointer_offset（协议勾选、物品格规则已使用）。
- **预期行为：** 先应用偏移求最终落点，再做 no_click/禁点/风险检查。
- **修复提交：** `01e9fe9`（2026-09-16，未推送）。ActionValidator 现对框中心与 `resolved_click_point`（偏移+clamp 后）双查禁点，诱饵检查也改用最终落点；EX06 转为正式回归测试（中心 (0.3,0.3) 安全、偏移 (0.25,0.25) 后落点 (0.55,0.55) 进禁区 → 拒绝；同一动作无偏移时通过）。
- **关闭证据：** test_pointer_offset_moves_final_point_into_no_click_region（修复前中心检查放行、现按最终落点拒绝）；全套件绿。

### F04 效果验证提前返回使 pending 永不超时（P1 · S+I · EX08）
- **触发条件：** 目标持续闪烁/OCR 不再锚定且无语义变化。
- **预期行为：** 所有分支共享绝对 deadline；稳定候选窗口可刷新但绝不刷新总 deadline；到期统一转 INEFFECTIVE/UNKNOWN。
- **修复提交：** 待 D05。 **关闭证据：** 待 T11 回归。

### F05 恢复预算不覆盖 BACK；0 也不禁用恢复（P1 · S+I · EX03）
- **触发条件：** max_recoveries=0 且 profile 允许 back；遇无效动作/循环。
- **预期行为：** run 级预算由组合根持有，所有恢复（含 BACK、高分辨率重试）共用；0=禁用全部恢复。
- **修复提交：** 待 D06。 **关闭证据：** 待 T15 回归。

### F06 continuous 对终止状态统一重建监督器（P1 · S · open；安全锁存部分已由 D02 关闭）
- **触发条件：** 反复 BLOCKED/FAILED；启动脚本非零退出无限重启。
- **预期行为：** 按终止原因分类（SUCCESS/USER_STOP/SAFETY_TRIP/MANUAL_REQUIRED/AUTH_FAILURE/TRANSIENT_FAILURE/RECOVERY_EXHAUSTED）；安全停止与预算耗尽不得自动复位。
- **修复提交：** D02 部分 `871cfc4`：安全 trip（含看门狗/急停）在 observe 循环触发 should_stop → 停止而非重建（回归测试 test_continuous_cannot_clear_safety_trip）；重建本身现在推进 run generation。**剩余归 D06：** 终止原因枚举与白名单重试、run 级恢复预算跨重建、启动脚本（run_mumu_autoplay.ps1）的退出码分类与有界退避重启。
- **关闭证据：** 待 D06 全量落地后回填。

### F07 region_digest 对部分颜色变化失明（P1 · S+I · EX01）
- **触发条件：** 变化主要落在未采样颜色通道/漏采像素。
- **预期行为：** 多通道空间降采样（整块 RGB 或亮度+色度），忽略 alpha/stride 填充；同尺寸/格式差异函数有规定。
- **修复提交：** 待 D05。 **关闭证据：** 待 T12 回归。

### F08 任务记忆 generation 与执行任务代数未统一（P1 · S）
- **触发条件：** 推理期间主线切换；“达到10级”→“达到20级”等相似文本。
- **预期行为：** 唯一 task_generation 来源；任务身份（对象/数量/等级）变化推进代数并使旧请求/动作/效果候选一致失效；模糊相似只抗 OCR 抖动不抹数字差异。
- **修复提交：** 待 D07。 **关闭证据：** 待 T17 回归。

### F09 无外部证据配置时可仅凭两次高置信 DONE 确认完成（P1 · S+I · EX02）
- **触发条件：** 未配置 goal-evidence，模型连续 DONE；compact 路径把 DONE 映射为 succeeded。
- **预期行为：** 无明确目标谓词→UNVERIFIED，不写 SUCCESS；证据须同 run/task/window/geometry + 单调新鲜 + 至少两次不同采集；显式 required_evidence 路径不受此影响。
- **修复提交：** 待 D05。 **关闭证据：** 待 T13/T14 回归。

### F10 429 异常分类与新闭环调用者不匹配（P1 · S）
- **触发条件：** 主模型或验证器返回 HTTP 429。
- **预期行为：** 统一 ProviderError 分类（rate_limit/auth/quota/timeout/transient5xx/invalid_request/unsupported_capability/malformed_output）；429 有界退避；401/403/配额停止而非无限重试。
- **修复提交：** 待 D08。 **关闭证据：** 待 T18/T19 回归（本地假服务）。

### F11 本地结构化校验比声明的 Schema 宽松（P1 · S+I · EX05/EX07）
- **触发条件：** bool/字符串数值/NaN/Infinity/confidence>1；混合 [0,1] 与 [0,1000] 坐标。
- **预期行为：** 严格消费端校验（类型/有限性/上界/额外字段/重复键）；coord_space 显式配置，整框单一坐标系；拒绝后才构造 GroundedAction。
- **修复提交：** 待 D08。 **关闭证据：** 待 T20 回归。

### F12 排队被当成真实动作；观察关联可能过旧（P1 · S）
- **触发条件：** 执行器最终拒绝/部分原语失败/慢推理超过 1s。
- **预期行为：** ExecutionReceipt 逐原语回执；accepted≠executed；效果 pending 仅在收到执行证据后开始且 deadline 基于实际执行时刻；记录 execution_observation 供训练对齐，保留 inference_observation 追溯。
- **修复提交：** 待 D04/D10/D14。 **关闭证据：** 待 T08/T09/T10 回归。

### F13 正常成功回复未及时更新 last_raw_reply（P2 · S）
- **触发条件：** 正常有效模型回复，尤其紧随修复/规则快路径。
- **预期行为：** per-request 回复摘要；修复另设 attempt 字段。
- **修复提交：** 待 D10。 **关闭证据：** 待 T23 回归。

### F14 高分辨率恢复未真正提高总览分辨率（P2 · S+I · EX04）
- **触发条件：** 640 宽、无目标裁剪配置进入 HIGH_RESOLUTION。
- **预期行为：** 重试真正提升有效像素（总览加倍或高清 ROI），记录前后尺寸/字节；已达上限则标无法升级并走不同策略。
- **修复提交：** 待 D05/D09。 **关闭证据：** 待回归。

### F15 会话持久化无作用域、无原子替换、无恢复后证据门槛（P1 · S/R）
- **触发条件：** 多 profile/多窗口；崩溃时写入；只读目录；损坏/过时状态。
- **预期行为：** profile_hash+namespace 隔离；版本化 JSON+大小上限；临时文件→fsync→os.replace 原子写；写失败可观察；恢复状态标 UNTRUSTED_RESTORED，未获两张新鲜帧证实不得触发操作。
- **修复提交：** 待 D07。 **关闭证据：** 待 T27 回归。

### F16 持续运行统计无限增长，录制阻塞采集发布（P2 · S/R）
- **触发条件：** 长时间运行、频繁看板刷新、编码/磁盘变慢。
- **预期行为：** 有界统计窗口（滚动 P95，全程值用固定内存估计并标注）；发布与录制解耦（不在发布锁内做编码/磁盘 I/O）；队列条目+字节双上限与背压 deadline；训练录制禁止静默丢记录。
- **修复提交：** 待 D11。 **关闭证据：** 待 T24/T25 回归。

### F17 当前 CI 的 Windows 路径字符串断言误报（P1 · C · tests_passed(local)）
- **触发条件：** 临时路径含 RUNNER~1，PowerShell 解析为 runneradmin。
- **预期行为：** DLL 路径按文件身份比较（samefile，两者存在时）+ 独立 SHA256 校验；保留负例（篡改 DLL、错误 anchor）。
- **修复提交：** `9da4eb6`（2026-09-16，未推送）。测试改为解析 .launched.env 的 DLL=/SHA= 字段：文件身份比较（samefile）+ 独立 SHA256 校验（pinned SHA 必须匹配 env 所指文件的实际字节）；新增短/长路径（GetShortPathNameW）、大小写、带空格目录、错误身份/缺失文件负例共 2 个新用例。未删除断言、未改 skip、未用 lower() 糊弄。
- **关闭证据：** 本地 `pytest tests/windows/test_release_bundle.py` 12 passed + 2 subtests；全套件 514 passed + 1 opt-in skip + 103 subtests；ruff/mypy 绿。根因核实：`scripts/run_bundle.ps1` 用 `Resolve-Path`（长名）设置 env，测试侧 `tempfile.mkdtemp()` 持短名形式——同一文件两种拼写。**CI 转绿需推送后 Actions 实跑确认（当前 NOT_RUN，按纪律不预标 PASS）。**

### G18 原生构建成功不等于 Python-Native 接口测试执行（P1 · C/S · tests_passed(local)）
- **触发条件：** 默认 CI 运行（Rust/Python 独立作业，无 DLL 传递）。
- **预期行为：** native-python-integration 作业显式构建 DLL 并校验 SHA 后执行 Native 相关 Python 测试；缺失=FAIL 而非 SKIP；硬件类 skip 与普通通过分开展示。
- **修复提交：** `8650d61`（2026-09-16，未推送）。新增 `native-python-integration` 作业：同作业内 `cargo build -p uga-capture --release --locked`（避免引入未验证的新第三方 action），DLL 存在性为硬前置（Test-Path 失败即 exit 1），导出 UGA_NATIVE_CAPTURE_DLL/SHA256 后运行 `pytest tests/windows/test_native_capture.py -q -ra`（-ra 显式列出 skip 原因）。全部 uses 沿用既有 40 位 SHA pin，persist-credentials:false，供应链策略测试 17 项全过。
- **关闭证据：** 本地模拟该作业全链路：DLL 校验→env 导出→5/5 原生测试通过（零 skip，1.32s）。**CI 实跑需推送后确认（NOT_RUN）。** 桌面能力类自 skip（WGC/DXGI unavailable on this desktop）仍属环境前提，以 -ra 原因展示。

### R19 模型网络出口、秘密与隐私边界不够显式（P1 · R）
- **触发条件：** 错误端点/重定向/上游回显/敏感截图入日志。
- **预期行为：** 远端默认仅 HTTPS+allowlist；回环 HTTP 单独例外；拒绝 userinfo/敏感 query/跨 origin 重定向；Key 仅入请求头；结构化脱敏日志；错误正文限额。
- **修复提交：** 待 D08/D13。 **关闭证据：** 本地假服务+脱敏测试（未复现前保持 R）。

### R20 低风险快规则缺少页面上下文授权（P1 · R · open；统一安全门已由 D03 落地）
- **触发条件：** 同名确认按钮出现在交易/协议/删除/登录等语境；协议勾选提案期置位。
- **预期行为：** 规则先判页面语境再交统一安全门；账户/协议/支付/删除/发送要求当前授权；勾选状态从执行/效果回执更新。补反例测试（支付/协议/删除/登录页）。
- **修复提交：** D03 部分 `01e9fe9`：所有规则/热点/恢复来源现在必须通过统一动作安全门（可信来源、生成守卫、最终落点、禁点+诱饵），不再有按标签/坐标免检的路径。**剩余归 D12：** 快规则的页面前置条件、风险类别、"确定"在支付/协议页的语境拒绝反例、协议勾选状态从回执更新。
- **关闭证据：** 待 D12 全量落地后回填。

### R21 原生调用存在缺少外层期限的阻塞边界（P1 · R）
- **触发条件：** 驱动/API 卡死、初始化不返回、采集与关闭竞争。
- **预期行为：** init/响应/关闭均有可观察 deadline；不可取消时受控进程隔离+有限退出；不释放仍在使用的 DLL 句柄。需注入卡住 worker/设备丢失/关闭竞争验证。
- **修复提交：** 待 D16。 **关闭证据：** 待故障注入（未复现前保持 R）。

### G22 训练/发布的正式能力与证据尚未完成（P1 · S）
- **触发条件：** 把 smoke checkpoint/接口 Protocol/历史报告当正式训练或 V1 完成。
- **预期行为：** 保留 CPU 基线并明确标签；五阶段真实 GPU 训练独立实现；缺项保持 blocked。
- **修复提交：** 待 D14/D15/D18。 **关闭证据：** 各阶段真实 artifact 与独立评估报告。

### C23 直接把运行时换成 GLM-5.3-Flash 会遇到思考参数不兼容（P1 · D/S）
- **触发条件：** 仅当用户选择运行时迁移到 glm-5.3-flash；当前默认 glm-4.6v 不构成缺陷。
- **预期行为：** capability profile 声明 thinking 策略；启动前拒绝 glm-5.3-flash + --vlm-no-thinking 组合；reasoning_content 不作为动作正文备用；content 空/未完成→弃权/模型错误。
- **修复提交：** 待 D09。 **关闭证据：** T22 回归 + （经授权后）Fixture 兼容探测。

## CI 基线事实（供 D01 复现）

- Actions run `35062922806`（基线 SHA）→ failure。
- 失败用例：`tests/windows/test_release_bundle.py::ReleaseBundleLauncherTests::test_clean_bundle_launches_agent_with_pinned_native_library`；启动返回码 0 与 launched 断言已通过，失败在 DLL 路径字符串比较（`RUNNER~1` 短名 vs `runneradmin` 长名）。
- 8 skip = 5 项 DLL 未构建 + 2 项双屏前提 + 1 项显式物理输入前提。
- Rust/UI 作业成功 ≠ Python-Native 集成被执行。
