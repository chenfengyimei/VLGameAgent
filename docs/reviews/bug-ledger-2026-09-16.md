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
| F02 | P0 | S | tests_passed(local) | ui_back/ui_close/ui_promote 标签被当信任依据 | `uga/agent/closed_loop.py::assess`（保留标签分支要求可信来源）；`uga/core/agent_loop.py`（跳过分支已删除）；`uga/policy/grounded_vlm.py::last_decision_source`（运行时赋值公开属性）；`uga/safety/action_gate.py`；`uga/agent/strategies/`（D12 规则清单化） | D03、D12 |
| F03 | P1 | S+I | tests_passed(local) | 禁点校验点(框中心)≠最终点击点(含偏移) | `uga/safety/action_gate.py::resolved_click_point`；`uga/agent/closed_loop.py::ActionValidator`（center+最终落点双查） | D03 |
| F04 | P1 | S+I | tests_passed(local) | 效果验证提前 return，pending 可能永不超时 | `uga/agent/closed_loop.py::observe`（稳定候选分支受硬 deadline 约束） | D05 |
| F05 | P1 | S+I | tests_passed(local) | 恢复预算不覆盖 BACK；max_recoveries=0 仍可 BACK | `uga/agent/recovery_budget.py`（新增 run 级预算）；`uga/agent/closed_loop.py::_request_recovery/_advance_loop_recovery`（预算门） | D06 |
| F06 | P1 | S | tests_passed(local) | continuous 对终止状态统一重建监督器，计数清零 | `uga/core/agent_loop.py`（预算耗尽门+重建上限 100）；`scripts/run_mumu_autoplay.ps1`（指数退避+最大 10 次+长跑重置） | D02、D06、D13 |
| F07 | P1 | S+I | tests_passed(local) | region_digest `[::16]` 抽样对部分颜色通道失明 | `uga/agent/closed_loop.py::region_digest`（多通道空间降采样） | D05 |
| F08 | P1 | S | tests_passed(local) | 任务记忆 generation 与主循环 _task_generation 未统一 | `uga/agent/session_state.py::GameSessionState.task_generation`（单一来源：任务身份变更推进）；`uga/core/agent_loop.py`（步首同步） | D07 |
| F15 | P1 | S/R | tests_passed(local) | 会话持久化无作用域/原子替换/恢复后证据门槛 | `uga/agent/session_state.py`（schema/namespace/原子写/隔离/UNTRUSTED_RESTORED）；`apps/agent/run.py`（profile.game_id 绑定） | D07 |
| F09 | P1 | S+I | tests_passed(local) | 无 required_evidence 时两次高置信 DONE 即判成功 | `uga/agent/closed_loop.py::GoalVerifier._consider_snapshot`（屏幕证据强制+上下文守卫） | D05 |
| F10 | P1 | S | tests_passed(local) | VisionRateLimitedError 未被新闭环统一捕获（只捕 BackendUnavailableError） | `uga/policy/vision_transport.py`（ProviderError 分类，BackendUnavailableError 子类——既有捕获边界全部兼容）；`uga/policy/vlm_planner.py`（fatal 传播） | D08 |
| F11 | P1 | S+I | tests_passed(local) | 本地结构化校验比声明 Schema 宽松（float() 强转、混合坐标整体除 1000、confidence 无上界/类型检查） | `uga/policy/structured_output.py`（严格数字/置信度/坐标/文本）；`uga/policy/grounded_vlm.py::_parse/_parse_action`、GroundedOutcomeVerifier（接线） | D08、D09 |
| F12 | P1 | S | tests_passed(local) | 排队被当真实动作；观察关联过旧（推理前 observation_id；数据侧默认 1s） | `uga/recording/episode_writer.py`（版本化回执表+执行观察关联）；`uga/recording/replay.py`（兼容读取/校验）；`uga/dataset/processor.py`（仅产出可验证执行正样本）；`uga/training/motor_pipeline.py`（训练溯源复核） | D04、D10、D14 |
| F13 | P2 | S | tests_passed(local) | 正常成功回复未更新 last_raw_reply，看板摘要空/陈旧 | `uga/policy/grounded_vlm.py::decide`（正常路径先写 last_raw_reply）；另补齐 4 个快路径出口漏打 _last_decision_source | D10 |
| F14 | P2 | S+I | tests_passed(local) | 高分辨率恢复未真正提高有效分辨率（仅改裁剪 padding） | `uga/policy/grounded_vlm.py::_images`（总览宽度加倍至 1280 封顶+非升级标记） | D05、D09 |
| F15 | P1 | S/R | tests_passed(local) | 会话持久化无作用域/原子替换/恢复后证据门槛 | `uga/agent/session_state.py`（schema/namespace/原子写/隔离/UNTRUSTED_RESTORED）；`apps/agent/run.py`（profile.game_id 绑定） | D07 |
| F16 | P2 | S/R | tests_passed(local; hub 部分) | CaptureHub 间隔统计无限增长全量排序；发布锁内同步录制 | `uga/capture/hub.py`（_gaps_ns→有界环+O(1) 累计器、录制回调移出发布锁）；`uga/recording/episode_writer.py`（有界，勿误伤） | D11 |
| F17 | P1 | C | tests_passed(local) | CI DLL 路径短名/长名字符串比较误报（RUNNER~1 vs runneradmin） | `tests/windows/test_release_bundle.py::test_clean_bundle_launches_agent_with_pinned_native_library` | D01 |
| G18 | P1 | C/S | tests_passed(local) | Python 作业缺 DLL，5 项 Native 测试静默 skip | `.github/workflows/ci.yml`（新增 native-python-integration 作业） | D01、D16 |
| R19 | P1 | R | tests_passed(local; 传输层) | 模型网络出口/秘密/隐私边界不显式 | `uga/policy/vision_transport.py`（HTTPS 强制回环例外/userinfo/凭据 query 拒绝、redact_error_detail 凭据脱敏）；`uga/policy/vlm_planner.py`（接线） | D08、D09、D10、D13 |
| R20 | P1 | R | open | “确定/提交”类 OCR 快规则缺页面语境授权；协议勾选提案期置位 | `uga/policy/grounded_vlm.py`（快规则）；`uga/agent/session_state.py` | D03、D12 |
| R21 | P1 | R | tests_passed(local; 可注入部分) | FFI init/响应/关闭缺外层 deadline（通道等待/线程 join） | `native/crates/uga-capture/src/ffi.rs`（响应 recv_timeout+初始化 deadline+poisoned 会话跳过 join；cargo test 验证）；`uga/capture/native_adapter.py` | D16 |
| G22 | P1 | S | open（D14 数据基线 tests_passed(local)；D15/D18 待） | 正式训练/发布能力与证据未完成（CPU smoke≠五阶段真实训练） | `uga/dataset/{processor,manifest,validator}.py`（资格数据基线）；`uga/training/behavior_cloning.py`、`uga/training/veomni.py`（正式训练仍待） | D14、D15、D18 |
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
- **D12 补充（`624ea60`，2026-09-16，未推送）：** 新增 `uga/agent/strategies/`——StrategyRule（name/source/game_id/summary/allowed_action/risk/effect/cooldown）+ StrategyRegistry（来源唯一性、game 归属校验、allows_fast_paths）+ registry_for(game_id)（未知游戏=空注册表）；mumu-xianyu 清单 14 条规则数据化（页面前置/允许动作/风险/效果/冷却）。GroundedVlmPlanner 接 `strategy_registry`：None=兼容默认（全部来源，单元测试）；显式注册表按 profile 圈定——run.py 对 mumu-xianyu 传满清单，**通用 profile 得到空注册表，全部游戏快路径禁用**（决策回落视觉模型）。测试证明：空注册表下 MuMu 弹窗取消快路径返回 None，注册后恢复；顺手修复弹窗取消分支漏打 `_last_decision_source` 的来源标记缺失（该来源曾永远显示 model）。

### F03 禁点校验点与最终点击点不同（P1 · S+I · EX06 · tests_passed(local)）
- **触发条件：** 目标框配置非零 pointer_offset（协议勾选、物品格规则已使用）。
- **预期行为：** 先应用偏移求最终落点，再做 no_click/禁点/风险检查。
- **修复提交：** `01e9fe9`（2026-09-16，未推送）。ActionValidator 现对框中心与 `resolved_click_point`（偏移+clamp 后）双查禁点，诱饵检查也改用最终落点；EX06 转为正式回归测试（中心 (0.3,0.3) 安全、偏移 (0.25,0.25) 后落点 (0.55,0.55) 进禁区 → 拒绝；同一动作无偏移时通过）。
- **关闭证据：** test_pointer_offset_moves_final_point_into_no_click_region（修复前中心检查放行、现按最终落点拒绝）；全套件绿。

### F04 效果验证提前返回使 pending 永不超时（P1 · S+I · EX08 · tests_passed(local)）
- **触发条件：** 目标持续闪烁/OCR 不再锚定且无语义变化。
- **预期行为：** 所有分支共享绝对 deadline；稳定候选窗口可刷新但绝不刷新总 deadline；到期统一转 INEFFECTIVE/UNKNOWN。
- **修复提交：** `a103d8e`（2026-09-16，未推送）。observe() 引入 `deadline_expired`（elapsed ≥ max(minimum, timeout)）：锚点首次翻转、像素瞬变重置、持续性等待三个分支到期后一律放行到统一超时解析；已稳定候选在到期时按 persistent 解析（有证据的成功），永久动画走 ineffective——候选刷新永不延长总期限。
- **关闭证据：** EX08 回归 test_effect_deadline_expires_under_permanent_pixel_animation（400ms 候选建立→800ms 瞬变刷新→1200ms 到期强制解析：pending False + ineffective=1）。既有 back-streak 测试曾依赖旧缺陷（冻结时钟+无视期限的候选等待），已按真实时间线修正（单调时钟推进到第二次恢复的真实签发时刻）。全套件 553+1 skip+103 subtests；ruff/mypy(179) 绿。

### F05 恢复预算不覆盖 BACK；0 也不禁用恢复（P1 · S+I · EX03 · tests_passed(local)）
- **触发条件：** 配置允许 back，且遇到无效动作或循环。
- **预期行为：** run 级预算由组合根持有，所有恢复（含 BACK、高分辨率重试）共用；0=禁用全部恢复。
- **修复提交：** `0e398c2`（2026-09-16，未推送）。新增 `uga/agent/recovery_budget.py`（fail-closed 计数器，limit=0 拒绝一切，1/2 覆盖每一种实际恢复）；ClosedLoopSupervisor 构造器接 `recovery_budget`（未传入时用 max_recoveries 建本地预算，语义不变）；_request_recovery 与 _advance_loop_recovery 的每个实际恢复（HIGH_RESOLUTION 与 BACK）在签发前消费预算，耗尽即 _stop_blocked（理由含 consumed/limit）；组合根 run.py 创建共享预算传入监督器与 agent_loop——监督器重建继承同一预算，会计不可重置。
- **关闭证据：** EX03 回归 test_zero_budget_blocks_every_recovery_including_back（max=0 + back 绑定 → BLOCKED、无 BACK 指令）+ test_budget_survives_supervisor_rebuild（重建后预算仍耗尽→blocked）+ 预算单元测试 + 诊断暴露 consumed/limit/exhausted。全套件 562 passed + 1 skip + 103 subtests；ruff/mypy(180) 绿。

### F06 continuous 对终止状态统一重建监督器（P1 · S · tests_passed(local)）
- **触发条件：** 反复 BLOCKED/FAILED；启动脚本非零退出无限重启。
- **预期行为：** 按终止原因分类（SUCCESS/USER_STOP/SAFETY_TRIP/MANUAL_REQUIRED/AUTH_FAILURE/TRANSIENT_FAILURE/RECOVERY_EXHAUSTED）；安全停止与预算耗尽不得自动复位。
- **修复提交：** D02 部分 `871cfc4`（安全 trip 不清零）；D06 部分 `0e398c2`：(1) continuous 重建门——预算耗尽且终止态非 SUCCEEDED 时停止（防止以观察频率自旋重建 blocked 监督器），重建硬上限 100 次/运行；(2) `scripts/run_mumu_autoplay.ps1` 崩溃重启改指数退避+抖动+最大 10 次，长跑（≥300s）后崩溃重置预算，退出 0 仍为用户停止。**剩余归 D13：** 终止原因完整枚举的看板呈现。
- **关闭证据：** test_continuous_budget_exhaustion_stops_the_rebuild_loop（工厂仅调用 1 次=拒绝重建）+ test_continuous_rebuilds_are_bounded（上限 3 → 工厂共 4 次=初始+3 重建后停止）+ D02 的 test_continuous_cannot_clear_safety_trip。全套件 562+1 skip+103 subtests 绿。

### F07 region_digest 对部分颜色变化失明（P1 · S+I · EX01 · tests_passed(local)）
- **触发条件：** 变化主要落在未采样颜色通道/漏采像素。
- **预期行为：** 多通道空间降采样（整块 RGB 或亮度+色度），忽略 alpha/stride 填充；同尺寸/格式差异函数有规定。
- **修复提交：** `a103d8e`（2026-09-16，未推送）。region_digest 替换 `[::16]` 为确定性空间网格采样（步长 isqrt(面积/4096)，采样数有界），每采样像素 2 字节：Rec.601 亮度（(29B+150G+77R)>>8）+ 通道 XOR——任何颜色通道变化必然可见，alpha 与 stride 填充被忽略；同尺寸/格式下摘要长度确定性相等。
- **关闭证据：** EX01 回归 test_red_green_channel_change_is_detected（B 常量、G↔R 互换：差异 >0.25 且 _target_changed=True——旧 [::16] 只抽到 B 通道完全失明）+ test_alpha_change_is_not_a_target_change（仅 alpha 变化摘要相等）+ test_digest_sampling_stays_bounded_on_huge_regions（1080p 全帧摘要 ≤ 有界采样）。全套件绿。

### F08 任务记忆 generation 与执行任务代数未统一（P1 · S）
- **触发条件：** 推理期间主线切换；“达到10级”→“达到20级”等相似文本。
- **预期行为：** 唯一 task_generation 来源；任务身份（对象/数量/等级）变化推进代数并使旧请求/动作/效果候选一致失效；模糊相似只抗 OCR 抖动不抹数字差异。
- **修复提交：** `4cc5dc9`（2026-09-16，未推送）。GameSessionState 新增 `task_generation`（单一来源）：任务采纳/身份变更（模糊同任务但 quest_level_target 不同，或对象变更）推进；agent_loop 步首同步 `self._task_generation = max(loop, session)`，随后 perception/请求/结果全部携带新代数——旧 outcome 经 generations_consistent 判 stale 丢弃；进度类变化（0/1→1/1）不推进；OCR 抖动走 _same_quest 同任务路径不推进。
- **关闭证据：** test_level_target_change_advances_the_task_generation（10级→20级两帧推进）、test_object_change_advances_the_task_generation、test_ocr_jitter_does_not_advance、test_progress_change_keeps_the_task_generation、test_stale_outcome_after_task_bump_is_discarded（pre-bump outcome vs post-bump snapshot → "stale" 丢弃，T17）。全套件 574 passed + 1 skip + 103 subtests；ruff/mypy(180) 绿。

### F09 无外部证据配置时可仅凭两次高置信 DONE 确认完成（P1 · S+I · EX02 · tests_passed(local)）
- **触发条件：** 未配置 goal-evidence，模型连续 DONE；compact 路径把 DONE 映射为 succeeded。
- **预期行为：** 无明确目标谓词→UNVERIFIED，不写 SUCCESS；证据须同 run/task/window/geometry + 单调新鲜 + 至少两次不同采集；显式 required_evidence 路径不受此影响。
- **修复提交：** `a103d8e`（2026-09-16，未推送）。GoalVerifier._consider_snapshot：(1) 删除 fallback_evidence 路径——模型自报 visible_text 不再是目标证据，屏幕 OCR 为空即 UNVERIFIED（两次高置信 DONE 什么都不确认）；(2) 一致性要求从「双方皆空也算一致」改为非空交集；(3) 候选携带 WindowIdentity+geometry/task generation，确认帧上下文不匹配则候选重置于新上下文（禁止跨窗口/任务拼接完成证据，T14）；(4) 两次不同帧+确认窗间隔原有要求保留。显式 required_evidence 路径语义不变。
- **关闭证据：** EX02 回归 test_done_without_screen_evidence_never_confirms + test_model_claimed_visible_text_is_not_goal_evidence + test_goal_evidence_cannot_span_window_or_task_context（窗口重建后候选重置，第 4 帧才在新上下文内确认）+ 更新后的双帧确认/成功转移测试改用屏幕证据。**运行影响：** 默认配置下 DONE 确认现需两帧真实屏幕 OCR 交集；OCR 空帧上的重复 DONE 保持 REOBSERVE（绝不 SUCCESS）。全套件绿。

### F10 429 异常分类与新闭环调用者不匹配（P1 · S）
- **触发条件：** 主模型或验证器返回 HTTP 429。
- **预期行为：** 统一 ProviderError 分类（rate_limit/auth/quota/timeout/transient5xx/invalid_request/unsupported_capability/malformed_output）；429 有界退避；401/403/配额停止而非无限重试。
- **修复提交：** `74ac447`（2026-09-16，未推送）。新增 `uga/policy/vision_transport.py`：ProviderErrorKind 九类 + ProviderError（**BackendUnavailableError 子类**——既有 except 边界全部兼容，F10 的漏接问题从根上消除）+ classify_provider_failure（401/403=AUTH fatal、429=RATE_LIMIT、402/Arrearage/欠费=QUOTA fatal、408=TIMEOUT、5xx=TRANSIENT、400/422 结构化标记=UNSUPPORTED_CAPABILITY）+ Retry-After 解析 + redact_error_detail 凭据脱敏。客户端 decide() 全部失败路径走分类；VlmPlannerPolicy 通用失败处理对 fatal=True 的 ProviderError 立即重抛（401/403/配额停止而非烧重试阶梯）。VisionRateLimitedError 保留为 RATE_LIMIT 别名（带 Retry-After）。
- **关闭证据：** test_vision_transport.py 27 项：401→AUTH fatal、429→RATE_LIMIT 带 Retry-After、402/欠费→QUOTA fatal、5xx 非致命、脱敏（Bearer/api_key/sk- 前缀）、后向兼容 isinstance。全套件 610 passed + 1 skip + 103 subtests；ruff/mypy(184) 绿。

### F11 本地结构化校验比声明的 Schema 宽松（P1 · S+I · EX05/EX07 · tests_passed(local)）
- **触发条件：** bool/字符串数值/NaN/Infinity/confidence>1；混合 [0,1] 与 [0,1000] 坐标。
- **预期行为：** 严格消费端校验（类型/有限性/上界/额外字段/重复键）；coord_space 显式配置，整框单一坐标系；拒绝后才构造 GroundedAction。
- **修复提交：** `74ac447`（2026-09-16，未推送）。新增 `uga/policy/structured_output.py`：strict_finite_number（拒绝 bool/str/NaN/Inf）、strict_unit_interval_number（[0,1] 置信度/坐标）、strict_confidence_value（缺席=None、畸形抛错——验证器 bool 置信度不再当 pass）、strict_coordinates（EX07：整框必须全 [0,1] 或全 (1,1000]，混合空间拒绝而非猜测缩放）、strict_bounded_text（长度上限）。接线：_parse 的 confidence、_parse_action 的 bbox/label/effect/confidence、GroundedOutcomeVerifier 的置信度判定。
- **关闭证据：** EX05/EX07 回归（bool 置信度拒绝、NaN 拒绝、混合坐标 [0.2,300,0.4,500] 拒绝、千坐标 (100,200,400,500) 正确归一）+ 单元 27 项；全套件 610+1 skip+103 subtests 绿。**额外字段/重复键拒绝归 D09 严格 Schema 阶段（当前 Schema 层未声明额外字段约束，实施需先冻结 Schema 变更）。**

### F12 排队被当成真实动作；观察关联可能过旧（P1 · S）
- **触发条件：** 执行器最终拒绝/部分原语失败/慢推理超过 1s。
- **预期行为：** ExecutionReceipt 逐原语回执；accepted≠executed；效果 pending 仅在收到执行证据后开始且 deadline 基于实际执行时刻；记录 execution_observation 供训练对齐，保留 inference_observation 追溯。
- **修复提交：** `d3341c2`（2026-09-16，未推送）。落地内容：(1) 新增 `uga/control/execution_receipt.py`：ExecutionReceipt（action_id/proposal_id/primitive/status/at/target/lease 身份/failure_reason + to_envelope 版本化行）与 aggregate_receipts（executed/partial/主导失败状态；全执行才叫 executed，混合即 partial）；(2) ActionScheduler 逐原语发布终态回执（EXECUTED/REJECTED/EXPIRED/FLUSHED，含异常路径），带界环 1024 + drain_receipts()，并补上此前执行器拒绝原语不进任何计数器的守恒缺口（rejected+=1）；(3) ClosedLoopSupervisor.record_execution_receipts 按 submitted_action_ids 精确匹配、每原语恰记一次、首个 EXECUTED 回执锚定效果时钟；observe() 执行证据门：期望原语未全部执行（含部分执行 PARTIAL、全部拒绝、回执迟到超时）一律解析为 not_executed——绝不进效果成功路径、不喂游戏级无效动作阶梯；连续 3 次未执行 → 有界停下（防失焦无限重提案烧模型调用）；(4) start_action/start_recovery_action 增 submitted_action_ids+expected_primitives（默认空 → 旧行为不变），agent_loop 步首回执泵（record_execution_receipts(drain_receipts())）在 observe 之前喂数据；(5) not_executed_actions/consecutive_not_executed_actions 进 diagnostics。
- **D14 数据侧修复（本提交，2026-09-17，未推送）：** EpisodeWriter 新增 `execution_receipts.parquet`，动作 provenance 显式保存 proposal/父 canonical 身份；回执泵同时送在线监督和录制器，下一新观察绑定为 execution_observation，inference_observation 保留追溯。Replay 对旧无表 Episode 只读兼容并标 legacy；DatasetProcessor 仅保留「非人工覆盖、全部物理原语 EXECUTED、存在新执行观察」的正样本，rejected/expired/flushed/partial 均排除。正式训练时长改为已验证 canonical 有效区间并集，等待时间、仅改 duration、无动作 Episode 均不计资格时长。Manifest 复核 episode_id/game_id/duration/quality/回执有效时长/视频内容摘要，拒绝跨 split 重复内容；正式训练再次从 Episode 重建可用样本，不能靠导出 JSONL 伪造正标签。
- **关闭证据：** 新增并通过 `test_rejected_and_partial_actions_excluded_from_positive_training_labels`、`test_fresh_execution_observation_round_trip`、`test_manifest_identity_duration_match_artifacts`、`test_cross_split_duplicate_episode_content_rejected`、`test_legacy_episode_cannot_satisfy_qualified_dataset_gate`。全套件 **622 passed + 1 opt-in skip + 103 subtests**；ruff 全仓绿；mypy strict **183 source files** 绿。F12 运行时与数据侧均完成本地门禁；CI/真实采集仍须推送或受监督运行后升级证据级别。

### F13 正常成功回复未及时更新 last_raw_reply（P2 · S）
- **触发条件：** 正常有效模型回复，尤其紧随修复/规则快路径。
- **预期行为：** per-request 回复摘要；修复另设 attempt 字段。
- **修复提交：** `872238b`（2026-09-16，未推送）。decide() 正常路径在 client 响应返回后、解析前写 `last_raw_reply = reply`——每次请求的摘要先于解析更新，修复路径原有赋值保留；T23 判别测试：修复后下一次正常决策的 last_raw_reply 必须是当次回复（不含旧 repair 文本）。**同批补齐 4 个快路径出口漏打 `_last_decision_source` 的同类缺陷**（修仙跳转/前往/摆摊物品格/ realms 外的 dialog 已在 D12 修）：来源缺失曾使 D03 可信来源门把规则产出当模型自报、看板来源列显示陈旧值。
- **关闭证据：** test_valid_first_reply_updates_current_request_summary + test_previous_repair_reply_does_not_leak_to_next_decision（T23）+ test_registered_game_still_fires_its_rule（弹窗取消来源标记判别）；全套件 612 passed + 1 skip + 103 subtests 绿。

### F14 高分辨率恢复未真正提高总览分辨率（P2 · S+I · EX04 · tests_passed(local)）
- **触发条件：** 640 宽、无目标裁剪配置进入 HIGH_RESOLUTION。
- **预期行为：** 重试真正提升有效像素（总览加倍或高清 ROI），记录前后尺寸/字节；已达上限则标无法升级并走不同策略。
- **修复提交：** `a103d8e`（2026-09-16，未推送）。_images 的 high_resolution_retry 现将决策帧总览宽度升至 min(2×max_image_width, 1280)（1280 为 CLI 校验硬顶）；新增 `last_high_resolution_upgraded` 公开标记：已达上限时记 False（非升级）而非假重试。裁剪 padding 逻辑保留。
- **关闭证据：** EX04 回归 test_high_resolution_retry_doubles_overview_width（1600px 帧：正常 640 → 重试 1280，PNG IHDR 宽度实测）+ test_high_resolution_retry_at_cap_is_recorded_as_non_upgrade（1280 封顶时 upgraded=False）+ 更新既有 cap 测试（1000px 帧在 1280 预算内按全宽发送）。全套件绿。

### F15 会话持久化无作用域、无原子替换、无恢复后证据门槛（P1 · S/R · tests_passed(local)）
- **触发条件：** 多 profile/多窗口；崩溃时写入；只读目录；损坏/过时状态。
- **预期行为：** profile_hash+namespace 隔离；版本化 JSON+大小上限；临时文件→fsync→os.replace 原子写；写失败可观察；恢复状态标 UNTRUSTED_RESTORED，未获两张新鲜帧证实不得触发操作。
- **修复提交：** `4cc5dc9`（2026-09-16，未推送）。_save_quest_memory：临时文件→flush/fsync→os.replace 原子替换，失败写 last_persistence_error（可观察不静默）；_load_quest_memory：64KiB 大小上限、`uga.session-state/1` schema 校验、profile_id 命名空间隔离（不匹配即忽略）、损坏/超限文件隔离为 `.corrupt-<ns>` 诊断证据并从空会话启动；恢复任务标 restored=True + verified_frames=0，restored_task_unverified 直到两帧新鲜同文本确认；agent_loop 在证实前不向规划器传 quest_text/quest_target_level（快路径无法被未证实记忆触发），context_summary 标注「未在本次运行中证实——仅参考」；run.py 绑定 profile.game_id 命名空间。
- **关闭证据：** test_atomic_write_leaves_no_torn_state（无残留 tmp、schema/namespace 落盘）、test_corrupt_state_is_quarantined_and_session_starts_empty、test_oversized_state_is_quarantined、test_profile_namespace_isolation、test_restored_task_verifies_after_two_fresh_frames、test_failed_save_is_observable_not_silent（T27）。全套件 574+1 skip+103 subtests 绿。**R 级余项：** 实机崩溃时写入的中间态、只读安装目录，仍需故障注入验证（保持 S/R）。

### F16 持续运行统计无限增长，录制阻塞采集发布（P2 · S/R）
- **触发条件：** 长时间运行、频繁看板刷新、编码/磁盘变慢。
- **预期行为：** 有界统计窗口（滚动 P95，全程值用固定内存估计并标注）；发布与录制解耦（不在发布锁内做编码/磁盘 I/O）；队列条目+字节双上限与背压 deadline；训练录制禁止静默丢记录。
- **修复提交：** `5cc4c45`（2026-09-16，未推送）。(1) `_gaps_ns` 列表 → `deque(maxlen=512)` 滚动环（P95 用环内窗口估计）+ `_gap_max_ns`/`_gap_count` O(1) 全程累计器——stats() 只对小窗口副本（锁外）排序，max 为全程精确值；(2) 录制回调移出发布锁：`_publish` 在锁内发布+计数，录制回调在独立 `_record_lock` 下串行执行（保持 episode 帧顺序）且不阻塞发布/可见性/统计；docstring 同步更新录制可见性语义。EpisodeWriter 既有内存上限未动。**第二批 `9e83066`（2026-09-17，未推送）：** ArtifactResourceLimits 新增 `max_recorder_queue_entries`(1024)/`max_recorder_queue_bytes`(64MiB) 双上限；RecorderChannel 重写——submit 接受 weight_bytes，字节预算超限即 TimeoutError（训练录制禁静默丢记录：生产者必须重试或失败），stats() 遥测（pending_entries/bytes/completed/rejected）；**关键修复：submit 不再持锁做阻塞 put**（worker 记录完成需同一把锁，否则队列满即死锁——测试曾挂死暴露此点）。日志轮转已在 D13 覆盖 supervisor.log。
- **关闭证据：** test_gap_statistics_stay_bounded_over_long_runs（>612 帧后窗口 ≤512、全程 max 精确、计数守恒）+ test_slow_recorder_does_not_block_latest_frame_publication（录制回调停摆时发布继续前进，accepted 增长判别）+ test_recorder_channel_declares_pending_bytes_cap / test_recorder_channel_telemetry_tracks_lifecycle / 既有 order-preservation 测试保持绿。全套件 617 passed + 1 skip + 103 subtests；ruff/mypy(184) 绿。

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
- **修复提交：** D03 部分 `01e9fe9`：所有规则/热点/恢复来源现在必须通过统一动作安全门（可信来源、生成守卫、最终落点、禁点+诱饵），不再有按标签/坐标免检的路径。D12 部分 `624ea60`：新增 `uga/agent/strategies/` 规则清单（StrategyRule：名称/来源/页面前置/允许动作/风险/效果/冷却）+ registry_for(game_id)，mumu-xianyu 14 条规则数据化，**通用 profile 得到空注册表、游戏快路径全禁用**；协议勾选规则显式标注 risk="agreement-tick (login page precondition)"（仅登录页同屏 开始游戏+同意用户协议 时适用）。**剩余归 D12 第二批：** 支付/删除页反例语料、协议勾选状态从执行/效果回执更新。
- **关闭证据：** test_strategies.py 9 项（空注册表禁用快路径、注册游戏规则照常、来源清单完备性、来源唯一/归属校验）+ D03 全部门禁负例保持绿。**支付/协议/删除页反例语料待 D12 第二批（NOT_RUN）。**

### R21 原生调用存在缺少外层期限的阻塞边界（P1 · R）
- **触发条件：** 驱动/API 卡死、初始化不返回、采集与关闭竞争。
- **预期行为：** init/响应/关闭均有可观察 deadline；不可取消时受控进程隔离+有限退出；不释放仍在使用的 DLL 句柄。需注入卡住 worker/设备丢失/关闭竞争验证。
- **修复提交：** `8ab5e9a`（2026-09-16，未推送）。ffi.rs 三处有界化：(1) `capture_frame` 响应等待改 `recv_timeout(timeout_ms + 2s grace)`——超时即置 `poisoned=true` 并断开命令通道（后续捕获立即 STATUS_INTERNAL 失败，不再排队于卡死 worker 后），返回 STATUS_TIMEOUT；(2) `create_handle` 初始化等待加 10s deadline——超时 detach worker（迟到的初始化完成会经断开的通道自行退出），返回 INTERNAL；(3) `uga_capture_destroy` 对 poisoned 会话**跳过 worker.join()**——故意泄漏卡死的 OS 线程（其若恢复会经断开通道自行退出），换取调用方有界关闭。
- **关闭证据（可注入部分）：** 3 项 Rust 故障注入测试（cargo test 7 全过）：无响应 worker→STATUS_TIMEOUT+poisoned（50ms~2.05s 有界）、后续捕获 STATUS_INTERNAL 快速失败（协议失步防护）、poisoned 会话的 destroy 有界返回（parked worker 永不 join）；重建 release DLL 后 Python 侧 5 项 native capture 测试全过。**NOT_RUN（需实机/授权）：** 真实驱动卡死注入、设备丢失注入、DPI/双屏/UIPI 矩阵、采集进程隔离架构（若泄漏线程不可接受）。

### G22 训练/发布的正式能力与证据尚未完成（P1 · S）
- **触发条件：** 把 smoke checkpoint/接口 Protocol/历史报告当正式训练或 V1 完成。
- **预期行为：** 保留 CPU 基线并明确标签；五阶段真实 GPU 训练独立实现；缺项保持 blocked。
- **D14 进展（本提交，2026-09-17，未推送）：** 已完成训练数据执行语义、legacy/unqualified 门、有效动作时长、manifest 实物身份复核及跨 split 重复内容拒绝；五项指定回归及全量本地门禁通过。**未生成或声称 5 小时正式语料，未执行真实 GPU 五阶段训练。**
- **剩余：** D15 五阶段真实 GPU 训练与独立评估、D18 正式资格证据/发布门仍为 open；各阶段必须提供真实 artifact，CPU smoke 不升级为正式训练证据。

### C23 直接把运行时换成 GLM-5.3-Flash 会遇到思考参数不兼容（P1 · D/S）
- **触发条件：** 仅当用户选择运行时迁移到 glm-5.3-flash；当前默认 glm-4.6v 不构成缺陷。
- **预期行为：** capability profile 声明 thinking 策略；启动前拒绝 glm-5.3-flash + --vlm-no-thinking 组合；reasoning_content 不作为动作正文备用；content 空/未完成→弃权/模型错误。
- **修复提交：** 待 D09。 **关闭证据：** T22 回归 + （经授权后）Fixture 兼容探测。

## CI 基线事实（供 D01 复现）

- Actions run `35062922806`（基线 SHA）→ failure。
- 失败用例：`tests/windows/test_release_bundle.py::ReleaseBundleLauncherTests::test_clean_bundle_launches_agent_with_pinned_native_library`；启动返回码 0 与 launched 断言已通过，失败在 DLL 路径字符串比较（`RUNNER~1` 短名 vs `runneradmin` 长名）。
- 8 skip = 5 项 DLL 未构建 + 2 项双屏前提 + 1 项显式物理输入前提。
- Rust/UI 作业成功 ≠ Python-Native 集成被执行。
