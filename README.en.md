<div align="center">

# 🎮 Universal Game Agent

### A general-purpose visual-agent runtime for real-time games and rich graphical interfaces

Turn vision models into closed-loop agents that observe, understand, act, and verify.

[![Chinese](https://img.shields.io/badge/语言-简体中文-e53935?style=for-the-badge&logo=readme&logoColor=white)](README.md)
[![Language](https://img.shields.io/badge/Language-English-2563eb?style=for-the-badge&logo=googletranslate&logoColor=white)](README.en.md)

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?style=flat-square&logo=windows11&logoColor=white)](https://www.microsoft.com/windows/)
[![Vision AI](https://img.shields.io/badge/Agent-Vision--Language-7C3AED?style=flat-square&logo=openai&logoColor=white)](#core-capabilities)
[![Safety](https://img.shields.io/badge/Safety-Fail--Closed-0F766E?style=flat-square&logo=shield&logoColor=white)](#safety-boundaries)
[![Tests](https://img.shields.io/badge/Tests-943%20Passed-16A34A?style=flat-square&logo=pytest&logoColor=white)](#engineering-quality)
[![License](https://img.shields.io/badge/License-MIT-F59E0B?style=flat-square&logo=opensourceinitiative&logoColor=white)](LICENSE)

[Quick Start](#quick-start) · [Full Guide](docs/usage.zh-CN.md) · [Project Copy](docs/PROJECT_INTRO.en.md) · [Agent Handoff Prompt](docs/AGENT_HANDOFF_PROMPT.zh-CN.md)

</div>

---

## What is Universal Game Agent?

**Universal Game Agent (UGA)** is a Windows-first, safety-first, model-driven visual interaction platform. It combines window capture, OCR, vision-language models, deterministic strategies, guarded input execution, and post-action verification so an agent can observe a live interface, understand its state, choose a visible target, perform a keyboard or pointer action, and confirm the result from the next frame.

UGA is neither a fixed-coordinate macro nor an automation script tied to a single title. It does not require private game APIs, process-memory inspection, or code injection. A target is defined by a compact YAML profile, reasoning is supplied by an OpenAI-compatible vision endpoint, and workflow-specific knowledge remains isolated in optional strategy modules.

> **In one sentence:** UGA is the engineering foundation that turns a vision model into a reliable, observable, and verifiable real-time game-control agent.

## Why UGA?

| Capability | What it delivers |
| --- | --- |
| 👁️ **Pixel-native perception** | Understand rendered frames and OCR text without entering the target process. |
| 🧠 **Model-and-rule collaboration** | Use visual reasoning for open-ended states and deterministic strategies for stable, high-frequency workflows. |
| 🎯 **Grounded single-step control** | Produce one current-frame action at a time and revalidate its target, coordinates, window, and freshness. |
| 🔄 **Post-action verification** | A click is not treated as success; the next observation must confirm the expected visual effect. |
| 🛡️ **Fail-closed execution** | Suppress input when the frame is stale, focus is lost, the window changes, the page is sensitive, or the target is ambiguous. |
| 📊 **End-to-end observability** | Inspect frames, OCR, decision sources, effects, safety suppressions, latency, and health in a local dashboard. |
| 🧩 **Multi-target extensibility** | Adapt to different windows, layouts, and interaction styles through profiles, strategies, Skills, and model adapters. |
| 🎞️ **Data flywheel** | Record Episodes, replay deterministically, qualify data, export samples, benchmark behavior, and optionally train policies. |

## Closed-loop architecture

```text
Target window
    │
    ▼
Capture ──► OCR / visual observations ──► VLM + strategy router
    ▲                                          │
    │                                          ▼
Effect verification ◄── guarded execution ◄── grounded action
                              │
                              ├── window identity and focus checks
                              ├── frame freshness and geometry checks
                              ├── no-click and sensitive-page gates
                              ├── expiring control lease
                              └── watchdog and emergency stop
```

The runtime follows **Observe → Understand → Plan → Ground → Execute → Verify → Recover**. The model can propose an action, but only the guarded runtime can authorize physical input. When the expected effect is absent, the system observes again, recovers, or stops instead of blindly repeating the action.

## Core capabilities

- **Multiple capture backends** with native acceleration, compatibility fallback, and continuous target identity checks.
- **OCR and temporal visual context** for dynamic text, layout, local targets, and state transitions.
- **OpenAI-compatible vision endpoints**, local or cloud, without locking the runtime to one provider.
- **Model-first and rules-first planning** for fast generalization and deterministic mature workflows.
- **Unified GUI action schema** for click, long-click, drag, scroll, keyboard, hotkey, typing, and wait actions.
- **Control arbitration and lifecycle management** for ownership, expiration, held-input release, and interruption.
- **Loopback diagnostics dashboard** at `http://127.0.0.1:8787` by default.
- **Reproducible Episodes** containing observations, decisions, actions, receipts, and terminal outcomes.
- **Agent-ready setup Skill** for repeatable installation, profile authoring, bounded launch, and troubleshooting.

## Quick start

### Requirements

- Windows 10/11 x64
- Python 3.11 or 3.12
- An OpenAI-compatible vision model endpoint that accepts images
- A visible target window that you are authorized to automate

### Install

```powershell
git clone https://github.com/chenfengyimei/VLGameAgent.git
cd VLGameAgent
.\install.cmd
.\check.cmd
```

### Create a target profile

```powershell
Copy-Item .\configs\games\generic-visual-game.example.yaml `
  .\configs\games\my-game.yaml
```

Replace every `CHANGE_ME` value with the exact executable and a narrowly anchored window-title pattern. Do not invent back, close, or ability hotspots that have not been verified on the real interface.

### Run a supervised five-minute session

```powershell
$env:UGA_VLM_API_KEY = "your-key"  # usually unnecessary for a local endpoint

.\start.cmd `
  -Profile .\configs\games\my-game.yaml `
  -Goal "Open the current objective and make one safe unit of progress" `
  -GoalEvidence "Objective complete" `
  -Model "your-vision-model" `
  -BaseUrl "https://provider.example/v1" `
  -DurationSeconds 300 `
  -Record .\runs\first-supervised-run
```

Open `http://127.0.0.1:8787` while the agent runs. Stop normally with `Ctrl+C`, or immediately latch control off with **`Ctrl+Shift+F12`**.

The first run must remain supervised. Use `-DurationSeconds 0 -Continuous` only after a bounded run proves that the target is unique and that no wrong-window input, repeated ineffective click, sensitive action, or unresolved page remains.

## Safety boundaries

Real input is intentionally stricter than model inference:

1. The profile must resolve to one intended process and window.
2. The latest frame, window generation, geometry, task generation, and focus are checked again before input.
3. Sensitive terms, no-click regions, and page-level rules can veto a model proposal.
4. Only the executor can emit physical input; the model cannot bypass the arbiter.
5. Control leases expire, held inputs are neutralized, and watchdog failures stop control.
6. Login, payment, purchase, identity, deletion, installation, trade, transfer, and account pages remain human-only.

This project is intended for research, testing, accessibility, and authorized automation. Operators remain responsible for applicable software terms, local law, account security, and data safety.

## Documentation

- [Chinese first-run guide](START_HERE.zh-CN.md)
- [Complete Chinese configuration and operation guide](docs/usage.zh-CN.md)
- [Standalone English project introduction](docs/PROJECT_INTRO.en.md)
- [Copy-ready Agent installation prompt](docs/AGENT_HANDOFF_PROMPT.zh-CN.md)
- [Architecture](ARCHITECTURE.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [中文 README](README.md)

## Agent Skill

The repository ships the [`universal-game-agent-setup`](skills/universal-game-agent-setup/SKILL.md) Skill. An agent with terminal and filesystem access can use it to safely clone or update the repository, install locked dependencies, author a target profile without guessed coordinates, connect a vision endpoint, launch a bounded supervised run, and troubleshoot from screenshots, events, and execution receipts.

## Engineering quality

The current main branch has passed:

- `943` Python tests and `545` subtests;
- full Ruff linting;
- strict Mypy type checking;
- TypeScript type checking and frontend build;
- Rust native-capture tests and release build;
- Skill structure and metadata validation.

Developer verification:

```powershell
$env:PYTHONPATH = "$PWD\.tooling;$PWD"
python -m ruff check .
python -m mypy uga apps
python -m pytest
npm ci
npm run typecheck
npm run build
```

## License

Released under the [MIT License](LICENSE).

---

<div align="center">

**Move beyond models that merely see—build agents that act safely, reliably, and verifiably.**

[⬆ Back to top](#-universal-game-agent)

</div>
