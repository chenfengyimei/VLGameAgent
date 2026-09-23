# Install and run workflow

Use this reference when the repository must be downloaded, installed, updated, or launched.

## Repository acquisition

If the user supplies a repository URL, use it. Otherwise the canonical public URL is:

```text
https://github.com/chenfengyimei/VLGameAgent.git
```

Before cloning, resolve the exact destination and ensure it does not contain unrelated files. For an existing checkout, inspect `git status` before updating. Do not discard local changes. Prefer `git pull --ff-only` only when the worktree is clean and the user asked for the newest revision.

## Windows installation

From the repository root:

```powershell
.\install.cmd
.\check.cmd
```

The installer creates `.venv`, installs hash-locked runtime and vision dependencies, installs the project, and runs tests. If it fails, report the first real failure rather than retrying indefinitely.

For a manual install:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-vision-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
```

## Model configuration

Collect or discover:

- OpenAI-compatible base URL.
- Vision-capable model identifier exposed by `/v1/models`.
- Whether the provider requires JSON-object mode or supports JSON Schema.
- Whether thinking output should be disabled.
- API key environment variable name.

Never echo, log, commit, or paste API keys. For a remote endpoint, place the key in `UGA_VLM_API_KEY` or the user-selected environment variable. A loopback endpoint normally requires no key.

## Preflight

Before physical input:

1. Confirm the target window is open, visible, and not on a login, payment, identity, purchase, deletion, trade, or account screen.
2. Confirm the profile no longer contains `CHANGE_ME`.
3. Confirm its executable and anchored title pattern resolve to the intended window only.
4. Run `.\check.cmd`.
5. Tell the operator the emergency hotkey is `Ctrl+Shift+F12`.
6. Start with a bounded 180-300 second supervised run.

## Launch

```powershell
.\start.cmd `
  -Profile .\configs\games\my-game.yaml `
  -Goal "Advance one safe unit of visible progress" `
  -GoalEvidence "Completion text" `
  -Model "vision-model" `
  -BaseUrl "https://provider.example/v1" `
  -DurationSeconds 300 `
  -Record .\runs\supervised
```

Open `http://127.0.0.1:8787` and inspect the first decisions. Use `-DurationSeconds 0 -Continuous` only after the bounded run has no repeated clicks, wrong-window capture, unresolved feature pages, or safety suppressions that need investigation.

## Troubleshooting evidence

Gather the current screenshot, latest event entries, decision source, target label, action effect, task text, screen type, feature page, recent failure, and last planner error. Diagnose from those facts. Do not broaden click permissions or remove safety gates just to make progress.
