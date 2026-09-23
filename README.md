# Universal Game Agent

> A safety-first, model-driven runtime that turns any visible game window into a verifiable computer-use environment.

Universal Game Agent (UGA) is a Windows-first visual interaction platform for games and other rich real-time interfaces. It captures pixels directly from a selected window, fuses OCR with a vision-language model, plans one grounded action at a time, executes keyboard or pointer input through a guarded control plane, and verifies the visual effect before continuing.

UGA is not a macro recorder and is not tied to one title, engine, emulator, UI layout, or model vendor. A target is described by a small YAML profile; reasoning is provided through an OpenAI-compatible vision endpoint; game-specific knowledge can remain optional, isolated strategy modules rather than assumptions baked into the runtime.

## Why UGA

- **Pixel-native understanding** — works from rendered frames instead of private game APIs, memory inspection, or injected code.
- **Grounded single-step control** — every model proposal identifies a visible target; the runtime revalidates window identity, geometry, freshness, focus, and click safety before physical input.
- **Layered intelligence** — combines OCR fast paths, general visual reasoning, optional verification, deterministic recovery, and reusable skills.
- **Fail-closed operation** — sensitive pages, stale frames, ambiguous targets, expired leases, focus loss, and watchdog failures stop or suppress input.
- **Model-provider freedom** — supports local or cloud vision models behind OpenAI-compatible APIs.
- **Observable by design** — a loopback dashboard exposes frames, recognized text, decisions, effects, suppression reasons, latency, and runtime health.
- **Reproducible engineering** — recordings, deterministic replay, dataset qualification, training utilities, benchmarks, and hash-anchored release evidence live in one repository.

## Runtime architecture

```text
Target window
    │
    ▼
Capture ──► OCR / visual observations ──► VLM + strategy router
    ▲                                          │
    │                                          ▼
Effect verification ◄── guarded execution ◄── grounded action
                              │
                              ├── window/focus/freshness checks
                              ├── no-click and sensitive-page gates
                              ├── expiring control lease
                              └── watchdog + emergency stop
```

The project also includes Episode recording, replay, dataset processing, optional motor-policy training, a deterministic test environment, qualification tooling, and browser-based diagnostics.

## Quick start on Windows

Requirements: Windows 10/11 x64 and Python 3.11 or 3.12.

```powershell
git clone https://github.com/chenfengyimei/VLGameAgent.git
cd VLGameAgent
.\install.cmd
.\check.cmd
```

Create a target profile:

```powershell
Copy-Item .\configs\games\generic-visual-game.example.yaml `
  .\configs\games\my-game.yaml
```

Edit `my-game.yaml` and replace every `CHANGE_ME` value with the exact executable and a narrowly anchored window-title pattern. Then launch a bounded five-minute run:

```powershell
$env:UGA_VLM_API_KEY = "your-key"   # omit for a local endpoint

.\start.cmd `
  -Profile .\configs\games\my-game.yaml `
  -Goal "Open the current objective and make one safe unit of progress" `
  -GoalEvidence "Objective complete" `
  -Model "your-vision-model" `
  -BaseUrl "https://provider.example/v1" `
  -DurationSeconds 300
```

Open `http://127.0.0.1:8787` while the agent is running. Stop normally with `Ctrl+C`; stop immediately and latch control off with `Ctrl+Shift+F12`.

For a local model, point `-BaseUrl` at the local OpenAI-compatible server and omit the API key. Use `-DurationSeconds 0 -Continuous` only after a bounded supervised run succeeds.

## Safety contract

Real input is intentionally stricter than model inference:

1. The profile must resolve to one target process and window.
2. The latest frame, window generation, geometry, task generation, and focus are checked again before execution.
3. Sensitive terms and configured no-click regions can suppress actions even when the model requests them.
4. The arbiter owns authorization; the executor is the only component allowed to emit physical input.
5. Every control lease expires, held inputs are neutralized, and the watchdog fails closed.
6. Payment, authentication, identity, deletion, installation, and account-transfer screens require the human operator.

Never begin an unattended run on a login, payment, identity, account, deletion, or purchase screen. Keep the emergency hotkey reachable.

## Documentation

- [中文快速开始](START_HERE.zh-CN.md)
- [中文完整介绍、配置与运行教程](docs/usage.zh-CN.md)
- [让另一个 Agent 自动完成安装运行的提示词](docs/AGENT_HANDOFF_PROMPT.zh-CN.md)
- [Architecture](ARCHITECTURE.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## Agent-installable skill

The repository ships a reusable Codex skill at [`skills/universal-game-agent-setup`](skills/universal-game-agent-setup). An agent can read that skill to clone or update the repository, install locked dependencies, create a safe target profile, perform preflight checks, launch a bounded run, and troubleshoot from evidence without exposing API keys.

## Developer verification

```powershell
$env:PYTHONPATH = "$PWD\.tooling;$PWD"
python -m ruff check .
python -m mypy uga apps
python -m pytest
npm ci
npm run typecheck
npm run build
```

Native capture and release qualification have additional platform prerequisites documented under `docs/runbooks/` and `docs/contracts/`. CI success proves software checks, not live-game accuracy or permission to control a machine.

## License

Released under the [MIT License](LICENSE).
