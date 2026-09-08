# UGA V1.1 Project Plan

This plan is derived from the approved UGA V1.1 design. Work proceeds in strict
dependency order; model training begins only after capture, safe control,
recording, and replay are reliable.

## Completed milestone: Capture Foundation

| Issue | Deliverable | Acceptance evidence |
|---|---|---|
| UGA-001 | Repository bootstrap | package layout, configuration, CI, runnable CLI |
| UGA-002 | Architecture invariants | invariants recorded and referenced by reviews |
| UGA-003 | Core schema versioning | versioned serialization envelope and contract tests |
| UGA-004 | Monotonic clock contract | ns clock, manual test clock, regression detection |
| UGA-005 | Event bus and lifecycle | start/pause/resume/stop/shutdown smoke path |
| UGA-006 | WindowIdentity contract | HWND reuse and process restart distinguishable |
| UGA-007 | Windows process/HWND discovery | Win32 backend behind `WindowBackend` |
| UGA-008 | DPI and coordinate spaces | normalized/image/client/screen conversions tested |
| UGA-009 | CaptureBackend contract | lifecycle, health, capability and frame contracts |
| UGA-010 | WGC backend | optional native-driver adapter with honest probing |
| UGA-011 | DXGI backend | optional native-driver adapter with honest probing |
| UGA-012 | Capture registry | capability-ranked selection and fallback |
| UGA-013 | Frame ring buffer | bounded, thread-safe latest-state-wins semantics |

The milestone passed architecture review. Full environmental soak qualification
remains a V1 release gate.

## Completed milestone: Safe Control Foundation

UGA-014 through UGA-029 are implemented and reviewed: semantic/canonical/
physical schemas, physical backend boundary, Win32 scan-code and pointer input,
optional gamepad routing, input state, focus/integrity policy, OS emergency
hotkey, proposal lifetime, leases, arbitration, expiration, 30 Hz scheduling,
and watchdog shutdown.

## Completed milestone: Recorder + Replay

UGA-030 through UGA-034 build the durable timeline and provenance layer needed
before baseline-agent work begins. The transactional Episode writer, typed
Parquet tables, source-timed MP4, checksums, lossless-ish recorder channel, and
deterministic replay engine have passed implementation review. A supervised
10-minute gameplay recording remains an environmental qualification gate.

## Completed milestone: Baseline Agent

UGA-035 through UGA-053 implement observations, belief and environment state,
mode routing, task/skill/planner layers, recovery and memory, a GUI provider
boundary, and the first rules-plus-VLM vertical slice. The implementation review
and a deterministic end-to-end Episode/replay integration test have passed.

## Implementation-complete milestone: Dataset and Fast Policy

UGA-054 through UGA-070 provide validators, processors, safe splits, evaluation,
structured action chunks, training backends, instruction/recovery records, a
reasoning gate, and DAgger collection. Implementation review passed. The real
corpus, checkpoints, closed-loop results, and supervised DAgger iteration remain
qualification gates.

## Implementation-complete milestone: Generalization and packaging

UGA-071 through UGA-074 provide the cross-game benchmark runner, dashboard,
replay debugger, installable Python artifacts, native capture DLL, bundle
launcher, hashed release manifest, and qualification checklist.

The requirements follow-up additionally provides executable Dataset, Benchmark,
Capture, Fixture World, and Qualification commands; persistent Dataset and
evidence manifests; complete offline metrics; adaptive cadence; and runtime
telemetry artifacts. A tested modular `RealtimeAgentLoop` now composes the
capture-to-safe-input Fast Policy path, and the dashboard supports an
authenticated loopback-only live server in addition to offline snapshots.
The three browser-facing tools now use a locked strict-TypeScript build as
required by the V1 technology selection, with npm type-check/build gates in CI
and release packaging.

## Active milestone: V1 qualification (UGA-075)

The release remains intentionally unapproved until the selected environments,
licensed dataset, trained checkpoints, benchmark matrix, clean-machine tests,
security/governance review, and repository license all have evidence. See
`docs/status/uga-v1-status.md` and `RELEASE_CHECKLIST.md`.

## Definition of done

Each issue needs implementation, unit and contract tests, relevant integration
coverage, documentation, telemetry, error handling, configuration, replay-
compatibility review, and no known critical defect. AI issues additionally need
offline, latency, and closed-loop benchmarks.

## Deferred decisions

- Code license: no license is asserted until the owner chooses one.
- Git remote, `dev` branch, commits, and pushes require repository/remote setup.
