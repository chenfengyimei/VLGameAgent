# UGA V1.1 Architecture

Status: implementation contract freeze through Generalization and Packaging.

## Product boundary

UGA is a generalist embodied computer agent for interactive virtual worlds. Its
primary input is screen pixels plus a natural-language goal, temporal history,
and agent memory. Its output is standard keyboard, mouse, or gamepad input. The
core must not depend on game source code, internal APIs, memory reads, navigation
meshes, or scripted routes.

V1 targets closed-loop operation in one 3D exploration environment, one
real-time control environment, and one GUI-heavy environment. Commercial games
are generalization experiments, never CI requirements.

## Invariants

1. **Slow Brain and Fast Brain are separate.** Planning and recovery are
   event-driven; motor control is latency-sensitive.
2. **Action semantics have three layers.** Semantic actions become canonical
   actions, which adapters convert to physical input.
3. **Raw HID remains an escape hatch.** Unknown games may use confirmed
   temporary bindings without polluting the canonical action space.
4. **The Action Arbiter is the sole input authorizer.** Producers only submit
   proposals under a valid control lease; `InputExecutor` is the sole component
   permitted to call physical input backends.
5. **Telemetry is training data.** Contracts must support replay, evaluation,
   DAgger, distillation, and debugging from the first implementation.
6. **There is one monotonic timeline.** `UGATime` is an integer nanosecond value;
   wall clocks and video timestamps never drive online control.
7. **Every action has a lifetime.** Future action contracts include creation,
   effective, and expiration timestamps; stale actions are never replayed.
8. **Latest state wins on runtime paths.** Slow consumers replace pending state
   rather than accumulating an unbounded FIFO. Recorder paths are separate.
9. **Control ownership uses expiring leases.** Mode classification cannot grant
   itself direct input ownership.
10. **Windows I/O stays behind native contracts.** Business logic depends only
    on clock, window, capture, input, and gamepad interfaces.

## Foundation dependency direction

```text
apps -> uga.core.runtime
             |
             +-> uga.time (ClockBackend, Timeline)
             +-> uga.windows (WindowBackend, coordinates)
             +-> uga.capture (CaptureBackend, registry, ring buffer)

Windows implementations -> contracts
contracts -X-> Win32 / image libraries / model providers
```

The `Frame` contract carries immutable metadata and an opaque `BufferHandle`; it
is never a PIL image. Consumers explicitly map or adapt the buffer.

## Capture pipeline

```text
WindowIdentity -> CaptureBackendRegistry -> selected backend
                                      |-> health/failure -> next backend
selected backend -> Frame -> FrameRingBuffer
                              |-> latest() for runtime
                              |-> snapshot() for bounded temporal context
```

Backend preference is WGC, DXGI Desktop Duplication, then a conservative GDI
fallback. WGC and DXGI are optional native-provider boundaries in this phase;
their Python adapters never silently impersonate another backend. The fallback
is intended for development and compatibility, not performance claims.

## Time contract

`UGATime` is non-negative `int64` nanoseconds. `PerfCounterClock` delegates to
Python's monotonic performance counter (QPC-backed on supported Windows Python),
while the native Rust crate calls QPC directly. `MonotonicTimeline` rejects a
clock regression instead of repairing or hiding it.

## Window and coordinate contracts

`WindowIdentity` combines HWND, PID, executable-path hash, process start time,
and window generation. Equality of HWND alone is insufficient.

Coordinate spaces are explicit: model-normalized, image pixel, client pixel,
window pixel, physical screen pixel, and logical screen pixel. Conversion uses
a frozen transform context containing crop/letterbox geometry, client origin,
window origin, and DPI scale. No backend may apply an undocumented transform.

## Safe control pipeline

```text
producer -> ActionProposal + lease generation -> ActionArbiter
         -> 30 Hz ActionScheduler -> InputExecutor -> InputBackend
                                          |
                                          +-> FocusGuard immediately before write
```

Semantic, canonical, and physical action contracts remain distinct. Only an
accepted proposal can enter the scheduler. The executor verifies exact target
identity, foreground ownership, integrity compatibility, agent-enabled state,
and the live lease immediately before every write. Expired work is dropped and
never backfilled; a guard failure releases held input and flushes future work.

The watchdog and the OS-level Ctrl+Shift+F12 emergency hotkey share a latched
shutdown path: pause the runtime, revoke all leases, flush the scheduler, and
neutralize keyboard, mouse, and optional gamepad state. No planner or model API
can reset that latch; resumption requires construction of a new runtime safety
boundary.

## Baseline agent pipeline

```text
Frames -> Observation + temporal context -> BeliefState
                                        -> ModeRouter (hysteresis)
Goal -> TaskGraph -> PlannerProvider -> SkillRegistry -> CanonicalAction
                                                    -> EnvironmentAdapter
GUI mode -> GuiAgent provider -> GuiControlBridge ----^       |
                                                             v
                                             Safe control + Recorder + Replay
```

The planner and skills cannot emit OS input. Planner output has a fixed subgoal/
mode/skill/condition schema, and skills may request only semantic or canonical
actions. Unknown environments use explicit user-confirmed profiles; V1 never
discovers controls by randomly pressing keys. Model roles are configuration-
driven and provider implementations are injected behind protocols.

Belief is explicitly an inference from pixels, not privileged state. Reflection
runs only on named failure events. Working, episodic, semantic, and procedural
memory share a local SQLite/JSON store without requiring a vector database.

## Dataset, Fast Policy, and release boundary

Episodes pass through validation before processing or split assignment. Split
ownership is grouped by episode, session, player, and game to prevent trajectory
leakage. Fast Policy outputs time-bounded canonical `ActionChunk` values and
therefore remains upstream of the existing arbiter and safety path; no model or
trainer owns a physical input backend.

UGA-Bench reports capture, inference, planning, and input latency alongside task
success, retries, intervention, safety stops, and resource use. Packaging emits
a wheel, source archive, native capture DLL, launcher, and hashed manifest. The
manifest is deliberately non-releasable until all environmental, dataset,
training, generalization, and governance evidence is present.

Dataset manifests persist episode/session/player/game split ownership, quality
status, duration, category, source revision, dataset-license metadata, and the
digest of each Episode checksum manifest. Offline evaluation covers movement,
camera, button F1, complete action chunks, mode labels, and reasoning-gate
classification. Policy cadence degrades only through the bounded
5/4/2.5/2 Hz ladder and never exceeds the configured action horizon.

`RealtimeAgentLoop` is the modular-monolith composition boundary. Its
observation task runs at the current policy cadence while `ActionScheduler`
runs in a separate 30 Hz latency domain. Every accepted Fast Policy chunk is
expanded into canonical ticks, adapted to physical actions, and sent through
the same lease/arbiter/executor path used by rules and GUI producers. Its
single-item realtime slot is distinct from the Episode recorder path.
If the policy raises `need_reasoning`, the loop revokes Fast Policy ownership,
flushes queued future ticks, emits `ReasoningRequested`, and produces no input
from that chunk; a Slow Brain integration must establish a new subgoal before
control resumes.

Every finalized Episode is also a runtime-telemetry superset containing
`run.json`, `planner.jsonl`, and `metrics.json`; these files are checksummed with
the replay assets. Qualification ledgers independently hash test evidence and
cannot approve an unversioned source tree.

The UGA Fixture World is a developer-owned example environment. It provides a
stable exact window title, pixel-visible state, movement, relative heading,
interaction, GUI mode, and reset without depending on a commercial game.

The dashboard has an offline artifact mode and a live loopback-only HTTP mode.
Live state is read-only except for the seven explicit operator commands, which
require an unguessable CSRF token and are routed through `OperatorControl`.
Dashboard, Replay Debugger, and Dataset Viewer interaction code lives in the
strict TypeScript UI package. Python emits escaped state/data and embeds the
versioned JavaScript build artifacts; CI type-checks and rebuilds those assets.
