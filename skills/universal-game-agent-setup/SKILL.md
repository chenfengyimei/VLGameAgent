---
name: universal-game-agent-setup
description: Install, configure, safely launch, or troubleshoot the Universal Game Agent repository for a user-selected Windows game and OpenAI-compatible vision model. Use when an agent needs to obtain the project, create a target profile, run preflight checks, start a bounded visual-control session, or diagnose launch/runtime failures; do not use for unrelated game automation projects.
---

# Universal Game Agent Setup

Deliver a working, evidence-checked installation without weakening the runtime's safety boundaries.

## Route the task

- For cloning, updating, dependency installation, model configuration, preflight, launch, and runtime troubleshooting, read [references/install-and-run.md](references/install-and-run.md).
- When creating or changing a target YAML profile, also read [references/profile-authoring.md](references/profile-authoring.md).

## Non-negotiable boundaries

- Preserve existing files and uncommitted Git changes. Never use destructive reset or checkout to make installation easier.
- Never expose, persist, print, or commit an API key.
- Do not invent window identity, controls, normalized hotspots, or completion evidence. Discover them locally or request the missing value.
- Begin with a short supervised run. Unlimited continuous operation requires a successful bounded run and user approval.
- Keep `Ctrl+Shift+F12` available as the emergency stop. Stop immediately on a wrong window, repeated ineffective click, sensitive page, or ambiguous target.
- Treat login, payment, purchase, identity, deletion, installation, trade, transfer, and account pages as human-only.

## Completion standard

Report the repository path and revision, installation and preflight results, created profile path, exact bounded launch command, dashboard URL, stop controls, any unverified assumptions, and the evidence behind each unresolved issue. Do not claim live success from unit tests alone.
