<div align="center">

# 🎮 Universal Game Agent

### 面向实时游戏与复杂图形界面的通用视觉智能体运行时

让视觉模型真正完成从「看见画面」到「理解状态」、从「规划动作」到「验证结果」的完整闭环。

[![Language](https://img.shields.io/badge/语言-简体中文-e53935?style=for-the-badge&logo=readme&logoColor=white)](README.md)
[![English](https://img.shields.io/badge/Language-English-2563eb?style=for-the-badge&logo=googletranslate&logoColor=white)](README.en.md)

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?style=flat-square&logo=windows11&logoColor=white)](https://www.microsoft.com/windows/)
[![Vision AI](https://img.shields.io/badge/Agent-Vision--Language-7C3AED?style=flat-square&logo=openai&logoColor=white)](#核心能力)
[![Safety](https://img.shields.io/badge/Safety-Fail--Closed-0F766E?style=flat-square&logo=shield&logoColor=white)](#安全边界)
[![Tests](https://img.shields.io/badge/Tests-943%20Passed-16A34A?style=flat-square&logo=pytest&logoColor=white)](#工程质量)
[![License](https://img.shields.io/badge/License-MIT-F59E0B?style=flat-square&logo=opensourceinitiative&logoColor=white)](LICENSE)

[快速开始](#快速开始) · [完整教程](docs/usage.zh-CN.md) · [项目介绍文案](docs/PROJECT_INTRO.zh-CN.md) · [Agent 安装提示词](docs/AGENT_HANDOFF_PROMPT.zh-CN.md)

</div>

---

## 项目定位

**Universal Game Agent（UGA）** 是一个 Windows 优先、安全优先、模型驱动的通用视觉交互平台。它通过窗口画面捕获、OCR、视觉语言模型、确定性策略与受控输入执行，让智能体能够像人一样观察实时画面、理解界面语义、选择可见目标、执行鼠标或键盘动作，并在新画面中验证动作是否真正生效。

UGA 不是固定坐标脚本，也不是只能服务于单一产品的自动化程序。它不依赖私有游戏接口、进程内存读取或代码注入；目标应用通过一份小型 YAML 档案描述，推理能力通过 OpenAI-compatible 视觉模型接入，特定流程可以作为可选策略模块扩展，而通用运行时始终保持独立。

> **一句话概括：** UGA 是把视觉大模型转化为可靠、可观察、可验证的实时游戏操作智能体的工程化基础设施。

## 为什么选择 UGA

| 能力 | 说明 |
| --- | --- |
| 👁️ **纯视觉理解** | 直接从目标窗口的渲染像素与 OCR 文本理解状态，无需侵入目标进程。 |
| 🧠 **模型与规则协同** | 视觉模型负责开放世界理解，确定性策略负责高频、稳定且可验证的关键流程。 |
| 🎯 **单步落地决策** | 每轮只生成一个基于当前画面的动作，目标、坐标、窗口状态和时效都会再次校验。 |
| 🔄 **执行后验证** | 不把“点击过”当作“完成了”；必须从后续画面确认状态变化后才继续。 |
| 🛡️ **失效即关闭** | 画面过期、焦点丢失、窗口变化、敏感页面或目标不明确时，默认抑制输入而不是盲目尝试。 |
| 📊 **全链路可观测** | 本地看板展示画面、OCR、决策来源、执行结果、拦截原因、延迟与运行健康度。 |
| 🧩 **面向多目标扩展** | 通过目标档案、策略注册表、Skill 和模型适配层扩展到不同窗口、布局与玩法。 |
| 🎞️ **数据闭环** | 支持 Episode 录制、确定性回放、数据资格校验、导出、基准测试与可选训练流水线。 |

## 闭环架构

```text
目标窗口
   │
   ▼
画面捕获 ──► OCR / 视觉观测 ──► VLM + 策略路由
   ▲                                  │
   │                                  ▼
效果验证 ◄──── 安全执行层 ◄──── 单步落地动作
                    │
                    ├── 窗口身份与焦点校验
                    ├── 帧新鲜度与几何校验
                    ├── 禁点区域与敏感页面门禁
                    ├── 有时限的控制租约
                    └── 看门狗与紧急停止
```

完整运行循环遵循 **Observe → Understand → Plan → Ground → Execute → Verify → Recover**。模型只负责提出候选动作，真正的物理输入必须经过运行时安全层授权；执行结果不符合预期时，系统会重新观察、恢复或停止，而不是无限重复点击。

## 核心能力

- **多后端窗口捕获**：支持原生高速捕获与兼容后端回退，并持续核验目标窗口身份。
- **OCR 与多帧视觉上下文**：结合界面文字、布局、局部目标和时间信息理解动态状态。
- **OpenAI-compatible 模型接口**：可连接本地或云端视觉模型，不绑定单一模型供应商。
- **模型优先与规则优先模式**：新目标可快速使用通用推理，成熟流程可使用确定性快路径。
- **GUI 与实时控制抽象**：统一描述点击、长按、拖拽、滚动、键盘、组合键与等待动作。
- **动作仲裁与生命周期管理**：控制权、按键释放、过期动作和异常中断均由统一控制平面管理。
- **本地诊断看板**：默认通过 `http://127.0.0.1:8787` 查看智能体当前状态。
- **可复现 Episode**：记录观察、决策、动作、执行回执与结果，便于排错、评审和数据生产。
- **Agent Skill 交付**：仓库自带安装与配置 Skill，可让其他编码智能体完成环境搭建和安全首跑。

## 快速开始

### 1. 环境要求

- Windows 10/11 x64
- Python 3.11 或 3.12
- 一个支持图片输入的 OpenAI-compatible 视觉模型服务
- 一个已启动、可见且允许自动化操作的目标窗口

### 2. 下载与安装

```powershell
git clone https://github.com/chenfengyimei/VLGameAgent.git
cd VLGameAgent
.\install.cmd
.\check.cmd
```

### 3. 创建目标档案

```powershell
Copy-Item .\configs\games\generic-visual-game.example.yaml `
  .\configs\games\my-game.yaml
```

编辑新档案，替换所有 `CHANGE_ME`，填写精确的进程文件名和窗口标题正则。未经实机确认，不要虚构返回、关闭或技能热点。

### 4. 配置模型并进行首次短测

```powershell
$env:UGA_VLM_API_KEY = "your-key"  # 本地模型通常可以省略

.\start.cmd `
  -Profile .\configs\games\my-game.yaml `
  -Goal "打开当前目标并安全推进一个步骤" `
  -GoalEvidence "目标完成" `
  -Model "your-vision-model" `
  -BaseUrl "https://provider.example/v1" `
  -DurationSeconds 300 `
  -Record .\runs\first-supervised-run
```

运行时打开 `http://127.0.0.1:8787` 查看实时看板。正常停止使用 `Ctrl+C`；紧急停止使用 **`Ctrl+Shift+F12`**。

首次运行必须保持有人监督。只有短测确认不存在错误窗口、重复点击、敏感操作或无法退出页面后，才应考虑使用 `-DurationSeconds 0 -Continuous` 长期运行。

## 安全边界

UGA 的物理输入权限故意比模型推理更严格：

1. 目标档案必须唯一匹配预期进程与窗口。
2. 输入前重新校验最新帧、窗口代次、几何、任务代次和焦点。
3. 敏感词、禁点区域和页面级规则可以否决模型动作。
4. 只有执行器能够产生物理输入，模型无法绕过仲裁器直接操作系统。
5. 控制租约会过期；异常时释放按键、停止动作并由看门狗关闭控制。
6. 登录、支付、购买、实名、删除、安装、交易、转移和账号设置页面必须交还人工处理。

本项目用于研究、测试、无障碍辅助和经授权的自动化场景。使用者必须遵守目标软件条款、当地法律以及账号和数据安全要求。

## 文档导航

- [第一次运行只看这一页](START_HERE.zh-CN.md)
- [完整中文介绍、配置与运行教程](docs/usage.zh-CN.md)
- [独立项目介绍文案](docs/PROJECT_INTRO.zh-CN.md)
- [可直接转发给其他 Agent 的安装提示词](docs/AGENT_HANDOFF_PROMPT.zh-CN.md)
- [系统架构](ARCHITECTURE.md)
- [安全策略](SECURITY.md)
- [贡献指南](CONTRIBUTING.md)
- [English README](README.en.md)

## Agent Skill

仓库内置 [`universal-game-agent-setup`](skills/universal-game-agent-setup/SKILL.md) Skill。具备终端和文件访问能力的 Agent 可以依据该 Skill：

- 安全克隆或更新仓库；
- 安装锁定依赖并运行预检；
- 创建不包含猜测坐标的目标档案；
- 接入本地或云端视觉模型；
- 启动有限时长的受监督运行；
- 根据画面、事件与执行回执诊断问题。

## 工程质量

当前主分支已通过：

- `943` 个 Python 测试与 `545` 个子测试；
- Ruff 全量静态检查；
- Mypy 严格类型检查；
- TypeScript 类型检查与前端构建；
- Rust 原生捕获模块测试与 Release 构建；
- Skill 结构与元数据校验。

开发者验证命令：

```powershell
$env:PYTHONPATH = "$PWD\.tooling;$PWD"
python -m ruff check .
python -m mypy uga apps
python -m pytest
npm ci
npm run typecheck
npm run build
```

## 许可证

本项目采用 [MIT License](LICENSE) 开源。

---

<div align="center">

**让模型不仅能看懂游戏，更能安全、可靠、可验证地完成操作。**

[⬆ 返回顶部](#-universal-game-agent)

</div>
