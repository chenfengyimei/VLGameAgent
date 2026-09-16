# VLGameAgent 审查覆盖台账（2026-09-16）

## 基线事实

| 项 | 值 |
|---|---|
| 审计基线 SHA | `34eba5ebe141862d590462c4551dbe0a2514d51a`（冻结时 = HEAD，`git status --porcelain` 为空） |
| CI（GitHub Actions run `35062922806`） | **failure** —— Python 作业 `1 failed / 504 passed / 8 skipped`（共 513 项） |
| CI 失败用例 | `tests/windows/test_release_bundle.py::ReleaseBundleLauncherTests::test_clean_bundle_launches_agent_with_pinned_native_library`（启动返回码 0 与 launched 断言已过；败于 DLL 路径 `RUNNER~1` 短名 vs `runneradmin` 长名的字符串比较 → F17） |
| CI 8 skip 构成 | 5 项 DLL 未构建（Python 作业未获得 Rust 构建产物 → G18）+ 2 项双屏前提 + 1 项显式物理输入前提 |
| 本机 Python | 3.12.14（codex 运行时，配合 gitignored `.tooling` site-packages via PYTHONPATH；CI 锁定为 3.11.9） |
| 本机 Node / Rust | node v24.15.0；cargo 1.97.1 |
| git 跟踪文件 | 共 302：uga 151、tests 67、apps 27、native 21、docs 20、configs 12、scripts 6、ui 4、third_party 2 等（`git ls-files` 可再生） |

## 本机基线门槛结果（2026-09-16，冻结 SHA 34eba5e）

命令与原始日志位于 `build/audit/34eba5e/baseline/`（gitignored；summary.txt / ruff.log / mypy.log / pytest.log / pytest-windows.log / python-nonphysical.xml）。CI 日志与本机日志分开记录，不合并计数。

| 门槛 | 结果 |
|---|---|
| ruff check . | PASS（0 违规） |
| mypy（pyproject packages=uga+apps，strict） | PASS（176 文件无错误） |
| pytest tests/unit tests/contracts tests/integration | PASS：492 passed + 87 subtests（16.4s） |
| pytest tests/windows | PASS：20 passed + 1 skipped（物理输入 opt-in）+ 2 subtests |

本机合计 512 passed + 1 skip（与 CI 的 513 项总数对得上：CI 里 1 failed（F17 路径误报）+ 5 项 Native DLL skip + 2 项双屏 skip 在本机均实际执行并通过；本机存在 DISPLAY2 与本地 DLL pin，故这些前提成立）。**CI 失败用例本机通过**，进一步证实 F17 为环境相关的路径字符串比较误报，而非启动器功能缺陷。

## 证据边界（本次审计没有做的事）

- 未在用户 Windows 环境运行完整仓库 pytest；未启动游戏或真实输入；未调用付费模型；未进行 GPU 训练或多显示器/UIPI 验证。
- 未完成所有文件的逐行审阅；审阅是跨模块、按安全关键链路（推理→新鲜度→评估→提交→执行→效果）深入。
- 8 项隔离检查（EX01–EX08）是从所读源码方法/分支提取或重放、使用模拟依赖的离线检查；`reproduced=true` 表示旧行为在隔离条件下重现，不是修复已通过，也不等价于完整应用复现。EX06 是几何谓词重放、EX08 是选定提前返回分支。
- 本地完整 git clone 因网络解析失败未完成；GitHub 文件引用全部固定到审计 SHA。
- 官方模型文档（GLM-5.3-Flash）为 2026-09-16 访问内容；运行时接入实施前需再次确认参数。

## 隔离检查（EX01–EX08）

| 检查 | 隔离结果 | 对应问题 | 仍需补做 |
|---|---|---|---|
| EX01 | 红/绿通道改变而抽样摘要不变 | F07 | 导入真实 Frame/region_digest 及不同 stride/format 测试 |
| EX02 | 空证据、间隔足够的两次高置信候选被确认 | F09 | 完整 GoalVerifier/监督器/CLI 默认路径 |
| EX03 | max_recoveries=0 仍产生 BACK 指令 | F05 | 真实预算状态机与物理恢复回执 |
| EX04 | 无裁剪时高清重试编码参数不变 | F14 | 实际 PNG 尺寸与模型输入记录 |
| EX05 | 验证器谓词接受 bool/字符串/Infinity/超过 1 值 | F11 | 完整 HTTP 响应解析及 strict Schema |
| EX06 | 框中心不在禁区，但偏移后的实际点进入禁区 | F03 | 完整 ActionGate 至 InputExecutor 路径 |
| EX07 | 混合坐标被整体缩放成合法框 | F11 | provider 适配器与实际 parser |
| EX08 | 持续像素变化分支在总期限后仍提前返回 pending | F04 | 完整 observe 状态机与真实时钟 Mock |

结论：8/8 重现的是**旧分支行为**；这些隔离检查必须转成导入真实仓库模块的 pytest 才能作为最终回归。

## 代码面覆盖表（已核对 vs 待补齐）

| 代码面 | 本次已核对 | 仍须补齐 |
|---|---|---|
| 架构/状态/旧计划 | ARCHITECTURE、SECURITY、RELEASE_CHECKLIST、旧审计与交接、当前 CI | 各契约文档逐项与最新修复 SHA 对齐 |
| CLI/真实组合 | apps.agent.run 关键配置、资源启动、stop、record、loop 组合 | 所有其他 apps 入口、所有异常路径 |
| core/闭环 | 推理→freshness→assess→提交、continuous、效果/目标/恢复关键方法 | 全部辅助函数和所有分支的集成覆盖 |
| control/safety | lease/arbiter/scheduler/executor/FocusGuard/Shutdown/Watchdog 实现 | SendInput/gamepad 底层、OS 竞态、硬件实测 |
| perception/policy | schema、模型传输、grounded 规划/解析/规则/验证器 | 全部 OCR 适配器、图像编码性能、FastPolicy 各后端 |
| session/rules | 任务记忆、持久化、主线/部分游戏规则 | 固定区域/规则的真实画面反例、其他 profile |
| capture | Hub、RingBuffer、背压和统计路径 | WGC/DXGI/GDI 全部错误路径与具体机器性能 |
| native | FFI 主要 create/capture/release/destroy 生命周期及源码树 | Windows 后端逐函数审查、ABI/驱动/双屏/UIPI 矩阵 |
| recording | EpisodeWriter、RecorderChannel 关键实现 | Replay/Video 完整异常矩阵、增量写入/关闭兼容性 |
| dataset | Processor、Manifest/split 隔离、artifact 校验入口 | Validator/OpenCUA/导入导出、manifest 与实物全面对照 |
| training | BehaviorCloningTrainer 全文、VeOmni 接口、状态文档 | motor pipeline/artifact/DAgger/recipes 逐函数与真实训练 |
| benchmark/release | 架构范围、正式门槛、launcher 测试与 CI | 全部资格工具代码及当前 SHA 真实证据 |
| UI/dashboard | 通用认证/前端 token 流程、只读运行面板、命令连接状态 | 全部 Replay/Dataset 前端、交互与浏览器并发回归 |
| 依赖/供应链 | pyproject、CI 固定引用与现有哈希机制 | 锁文件逐项许可证/漏洞/来源核查、release 独立可信 anchor |

## 文件清单与审阅状态

标记：★ = 本次审计深入核对；◐ = 部分核对（关键方法/被调用面）；无标记 = 待补齐（在对应 D 任务执行时逐文件补记审阅范围/不变量/调用关系/已有测试）。清单可由 `git ls-files` 重新生成。

### apps/（27）
★ run.py（apps/agent）、dashboard.py（apps/agent）、__main__.py（apps/dashboard）；
◐ 其他 CLI 入口（apps/benchmark、build_evidence、capture_probe、dataset、dependency_inventory、example_game、qualification、release_manifest、replay、training 等 __main__.py）

### uga/agent/
★ closed_loop.py、session_state.py；◐ progress.py；
belief.py、memory.py、mode_router.py、planner.py、recovery.py、skills.py、task_graph.py、vertical_slice.py（待审）

### uga/core/
★ agent_loop.py、errors.py（经调用面）；◐ config.py、events.py、lifecycle.py、runtime.py、schema.py；
artifact_limits.py（待审）

### uga/safety/
★ shutdown.py、watchdog.py、focus_guard.py；◐ emergency_stop.py

### uga/control/
★ arbiter.py、executor.py、lease_manager.py、scheduler.py、lease.py、canonical.py、expiration.py、input_state.py、lifetime.py、physical.py、proposal.py、semantic.py（关键不变量经调用面核对）；
gamepad_backend.py、input_backend.py、windows_input.py（底层待硬件实测）

### uga/policy/
★ grounded_vlm.py、vlm_planner.py；◐ decision_journal.py、fast_policy.py、scripted_tap.py、reasoning_gate.py；
action_chunk.py、cadence.py、chunk_controller.py（待审）

### uga/perception/
★ schema.py；◐ builder.py；text.py（待审）

### uga/capture/
★ hub.py；◐ ring_buffer.py、native_adapter.py、base.py、frame.py、fallback.py、registry.py、session.py、windows_graphics_capture.py、dxgi.py、native_ctypes.py、diagnostics.py（错误路径待补）

### uga/recording/
★ episode_writer.py；◐ schema.py、video.py、replay.py；debugger.py、json_codec.py、parquet_io.py（待审）

### uga/dataset/
★ processor.py、manifest.py；◐ validator.py、builder.py；opencua.py、viewer.py（待审）

### uga/training/
★ behavior_cloning.py、veomni.py；artifact.py、dagger.py、datasets.py、motor_pipeline.py（待审，G22 正式路径缺口）

### uga/dashboard/ · uga/gui/ · uga/ui/
★ server.py（uga/dashboard）、dashboard.ts（ui/src）；◐ controller.py（uga/dashboard）、state.py、gui/controller.py、gui/control_bridge.py、gui/agent.py、gui/schema.py；static/*.js（待补浏览器并发回归）

### uga/windows/ · uga/environment/ · uga/observation/ · uga/evaluation/ · uga/benchmark/ · uga/release/ · uga/models/ · uga/time/
◐ window_identity.py、backend.py、coordinates.py、dpi.py、integrity.py；environment/profile.py、generic.py、adapter.py；其余按 D 任务对应范围补齐（release 面随 D17/D18）

### native/
★ ffi.rs（主要生命周期）；◐ wgc.rs、dxgi.rs、windows/mod.rs 及其余 crate 文件（逐函数审查待 D16）

### tests/（67）
★ test_release_bundle.py（windows）、test_dashboard_server.py（integration）；其余测试文件按“已有测试”视角经被测模块核对，未逐行审阅

### scripts/ · configs/ · CI
★ run_mumu_autoplay.ps1、.github/workflows/ci.yml、configs/games/mumu-xianyu.yaml（经快规则引用面）、pyproject.toml；
◐ build_native.ps1、build_release.ps1、run_bundle.ps1、setup_windows.ps1、qualification/run_vlm_mumu_matrix.ps1；其余 configs 待审

### 文档
★ ARCHITECTURE.md、SECURITY.md、RELEASE_CHECKLIST.md、GLM53_HANDOFF_PLAN.md、docs/status/uga-v1-status.md、docs/reviews/post-glm-implementation-audit.md、docs/guides/mumu-vlm-closed-loop.zh-CN.md；其余 docs/contracts、runbooks 待与修复 SHA 对齐

## 纪律提醒

- 在上述未审项补齐之前，不出具“全项目无其他 bug”结论。
- 每个后续 D 任务开工前，先在本台账登记将改文件与不可破坏的不变量；完成后回填修复 commit 与关闭证据。
- CI 结果与本机结果分开记录；skip 必须写明前提（DLL 未构建 / 双屏 / 物理输入 opt-in）。
