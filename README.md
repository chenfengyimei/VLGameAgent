# Universal Game Agent (UGA)

UGA is a Windows-first, generalist game computer-use agent. It observes pixels,
reasons about goals, selects skills, and emits keyboard, mouse, or gamepad input
through explicit safety and ownership boundaries.

**[中文完整介绍与使用教程](docs/usage.zh-CN.md) — recommended for first-time users.**

For the local Qwen3-VL + MuMu closed loop and its live visual dashboard, use the
step-by-step [Chinese MuMu VLM guide](docs/guides/mumu-vlm-closed-loop.zh-CN.md).

The repository implements the full observe→decide→control→record→dataset→train→
benchmark pipeline, including three capture backends (WGC, DXGI duplication,
GDI fallback), lease-arbitrated input with watchdog and emergency-stop safety,
transactional Episode recording with deterministic replay, dataset tooling with
quality gates, deterministic motor-policy training, UGA-Bench, offline
dashboard/replay/dataset UIs, a developer-owned Fixture World, qualification
evidence tooling, and verified Windows development packaging. The project is
licensed under [MIT](LICENSE).

## Runtime hardening and data compatibility

The [2026-09-17 module delivery](docs/reviews/runtime-hardening-completion-2026-09-17.md)
covers bounded recording/inference, sensitive-page handoff, causal GUI exports,
native lifetime guards and remaining live qualification. Console and direct
run entries share one parser, including `--decision-timeout-seconds`. Bounded
goal claims require `--goal-evidence`. Use `uga-dataset gui-export EPISODE
--output NEW_DIRECTORY` for receipt-qualified GUI examples.

Existing recordings stay immutable. Only actual pre-action evidence qualifies
training inputs and demonstrated duration. CI does not establish physical-input
latency, long-running device reliability or five-stage GPU model training.

## PR2/PR3 integration notes

The [conflict-resolution contract](docs/reviews/pr3-conflict-resolution-2026-09-17.md)
preserves the PNG `gui-export` command and adds `gui-export-references` for
reference-only JSONL. Evidence coverage and active execution are reported as
separate durations; neither uses action TTL. Both public run entries accept
`--decision-timeout-seconds` and `--perception-timeout-seconds`.

## Architectural rules

- Slow reasoning and real-time control are separate.
- Semantic, canonical, and physical actions are separate.
- The action arbiter is the only authorizer; the input executor is the only
  physical-backend writer.
- Runtime telemetry is training data.
- All online timing uses one monotonic nanosecond timeline.
- Every future action has a lifetime; expired actions are dropped.
- Runtime consumers use latest-state-wins backpressure.
- Control ownership is represented by expiring leases.
- Windows I/O is hidden behind backend contracts.
- Raw HID remains a future escape hatch for unknown environments.

See [ARCHITECTURE.md](ARCHITECTURE.md) and [PROJECT_PLAN.md](PROJECT_PLAN.md).

## Current quick start

UGA requires Python 3.11 or newer. Install the project (or its wheel) to obtain
the Parquet, video, and YAML runtime dependencies.
Developers changing Dashboard, Replay, or Dataset Viewer code also need Node.js
24 and should run `npm ci && npm run typecheck && npm run build`; wheel users do
not need Node because the compiled browser assets are included.

```powershell
python -m pip install -e .
uga-agent
uga-dashboard --output dashboard.html
uga-dashboard --serve --host 127.0.0.1 --port 8765
uga-example-game --headless-smoke
uga-capture-probe --help
uga-dataset --help
uga-benchmark --help
uga-qualify --help
uga-train --help
python -m pytest
```

`uga-agent` performs a lifecycle smoke run. Physical input remains disabled until
an operator explicitly enables a runtime with a safe environment manifest,
exact target identity, live control lease, and active emergency hotkey.

The modular `RealtimeAgentLoop` composes capture, latest-state observation,
mode routing, Fast Policy, control leases, arbitration, 30 Hz scheduling,
telemetry, and optional Episode recording. The live dashboard binds only to a
loopback IPv4 address, requires the exact bound `Host` authority, enforces a
same-origin `Origin` on browser command requests, and authenticates operator
commands with a per-process CSRF token.

Windows development bundles are built with `scripts/build_release.ps1`. Install
the bundled wheel, then use `run_uga.ps1` to point the runtime at the bundled
native capture DLL. Direct native-capture development runs must also set
`UGA_NATIVE_CAPTURE_SHA256` to the lowercase SHA-256 of that DLL; the digest is
checked before any library code is loaded. Consult `release-manifest.json`
before interpreting a bundle as qualified. A successful build also writes an
external, source-bound `build-qualification.json` beside the bundle evidence
directory after exercising the real bundled launcher.

The developer-owned Fixture World provides a deterministic visual target for
supervised Windows testing. See
[docs/runbooks/fixture-qualification.md](docs/runbooks/fixture-qualification.md).
Qualification evidence is hash-anchored according to
[docs/contracts/qualification-evidence.md](docs/contracts/qualification-evidence.md).
