# Changelog

## Unreleased

- Bootstrap the UGA V1.1 Capture Foundation repository.
- Freeze architecture, schema, time, window, coordinate, capture, and ring
  buffer contracts.
- Add Python runtime lifecycle/events and native Rust QPC/window/frame metadata
  crates.
- Add production WGC and DXGI/D3D11 capture providers, a panic-contained C ABI,
  Python ctypes driver, GDI development fallback, backend selection/failover,
  and capture telemetry.
- Add three-layer action contracts, leases, arbitration, 30 Hz scheduling,
  expiry, stateful SendInput keyboard/mouse support, optional virtual gamepad
  routing, focus/integrity guards, environment policy, watchdog, and a latched
  Ctrl+Shift+F12 emergency stop.
- Add transactional Episode recording, explicit Parquet schemas, full action
  provenance, raw-input state snapshots, source-timed H.264 video, checksums,
  and deterministic read-only replay.
- Add observation/temporal/belief contracts, safe generic game profiles,
  hysteretic mode routing, skills and task graphs, Qwen-compatible planner and
  UI-TARS-compatible GUI provider boundaries, SQLite memory, event-triggered
  recovery, configuration-driven model roles, and a recorded baseline closed
  loop.
- Add Episode validation/processing/viewing, leakage-safe splits, offline and
  closed-loop evaluation, structured Fast Policy action chunks, Qwen feature and
  temporal decoder boundaries, deterministic BC/VeOmni training surfaces,
  instruction/recovery data, reasoning gating, and DAgger collection.
- Add UGA-Bench contracts and aggregation, an offline operator dashboard,
  replay debugger UI, installable console scripts, Windows bundle launcher,
  hashed development release manifests, and explicit qualification gates.
- Add license-aware persistent Dataset Manifests, producer-clock/input-gap
  quality checks, complete offline policy/classification metrics, bounded
  dynamic inference cadence, per-game/per-split benchmark reporting and human
  baselines, runtime planner/metrics telemetry, hash-verified qualification
  ledgers, executable Dataset/Benchmark/Capture/Qualification tools, and a
  developer-owned Fixture World.
- Add an OpenCUA AgentNetBench trajectory import/export boundary, executable
  deterministic motor-head training, canonical/physical action-layer
  recording, and ActionChunk-to-arbiter control routing.
- Add a modular capture-to-safe-input realtime agent loop and a loopback-only
  live dashboard server with authenticated operator commands. Reasoning-gate
  requests now revoke ownership and cancel future motor ticks before any new
  Slow Brain decision.
- Make capture qualification diagnostics streaming and add bounded-FPS probing,
  preventing long soak runs from retaining full-resolution frame buffers.
- Bind motor-training samples to recorded Episode/Observation/Action provenance,
  reject non-train or non-accepted sources, and hash-verify every checkpoint and
  training input through `uga-train verify`.
- Move Dashboard, Replay Debugger, and Dataset Viewer interactions into a strict,
  locked TypeScript build and include its npm build dependencies in release
  license inventories.
- Add a read-only `uga-qualify preflight` report for Git revision, repository
  license, GPU, Dataset volume/splits, artifact integrity, and outstanding gates.
- Pin CI actions to immutable commit SHAs with least-privilege permissions and
  non-persisted checkout credentials, pin exact Python/Node/Rust patch
  versions, and install Python dependencies from a committed transitive
  hash-locked requirements file with `pip --require-hashes`, `--no-deps
  --no-build-isolation` project installs, `--no-isolation` artifact builds,
  and `--locked` Cargo operations; supply-chain regressions are rejected by
  repository policy tests.
- Enforce decoded Parquet byte limits through incremental batch reads, bound
  game-profile and model-registry YAML inputs, fail closed on deeply nested or
  oversized untrusted JSON documents instead of crashing, and stream
  qualification evidence hashing instead of loading whole files into memory.
- Keep watchdog enforcement alive when a liveness probe raises and complete the
  emergency neutralization sequence even when the clock backend fails.
- Deliver the dashboard CSRF token out of band through the console URL fragment
  instead of serving it inside the page to unauthenticated loopback requests.
- Verify and load the native capture DLL from a private per-process copy,
  closing the digest-to-load race, and abort loudly when an explicitly pinned
  native library fails verification instead of degrading to "not installed".
- Reject weak caller-supplied dashboard CSRF tokens, pin the dashboard CSP to
  the exact inline script digest instead of `unsafe-inline`, require a full
  git commit hash for development manifest revisions, and bound the cargo
  dependency inventory subprocess with a timeout.
- Add an optional out-of-band `-ManifestSha256` anchor to the bundle launcher:
  the manifest itself is verified first when supplied, and operators are warned
  that verification is otherwise self-referential.
- Add developer-owned DX11, DX12, and OpenGL fixture windows with deterministic
  color cycling and resize-safe swap chains, giving the capture qualification
  matrix real D3D and OpenGL render targets beyond the Tk-drawn Fixture World.
- Add a Vulkan capture fixture on a raw `vulkan-1.dll` loader whose ABI types
  and constants mirror `vulkan_core.h`, with per-image present semaphores,
  acquire-fence sync, and bounded acquire/fence waits so occlusion-stalled
  presents exit visibly instead of hanging; live-verified through WGC and
  DXGI capture with mid-capture resize tracking.
- Copy the overlapping sub-rect when a Windows Graphics Capture frame pool
  lags a window resize instead of failing with an unsupported-state error, so
  growing targets keep capturing across the pool recreation.
- Add a live watchdog-timeout exercise to the fixture qualification command:
  `--exercise-watchdog-timeout` deliberately stalls a dedicated supervision
  stack built from the production watchdog, monitor, lease, and shutdown
  classes and verifies the trip is fail-closed (watchdog cause, agent
  disabled, lease revoked, clean cleanup), recorded alongside the focus-loss
  and emergency-hotkey results.
- Automate launcher integrity, rollback, and removal evidence: live tests run
  the bundle launcher against synthetic bundles with production-built
  manifests, asserting every corrupted or missing artifact is refused before
  the agent starts, a failed candidate leaves the previous bundle runnable,
  and removal leaves no persistent environment state.
- Abort fixture qualification loudly when another window covers the fixture's
  resume-click point: the point resolves to its root owner before inputs are
  scheduled, turning a silent wrong-window click (world stuck in its GUI
  state, degraded occluded capture) into an operator-actionable failure.
- Select the MIT license for the repository: add the LICENSE file, declare the
  SPDX expression in the package metadata, ship the LICENSE in release bundles,
  and let the repository-license gate pass once the file is present instead of
  staying blocked on an owner decision. The dependency inventory declares only
  permissive licenses (MIT, Apache-2.0, BSD-3-Clause, Unicode-3.0), so nothing
  downstream restricts attribution-only redistribution.
- Record the V1 generalization claim scope: the owner accepted the
  fixture-scenario scope, so the generalization-bench gate is satisfied by the
  four developer-owned Fixture scenarios (Train A/B/C plus the locked
  `heldout_diagonal` Test D) and V1 claims cross-environment generalization
  within owned fixtures only; real licensed-game data is explicitly deferred
  to V2, and the claim wording in the architecture and evidence contracts is
  updated to prevent overclaiming.
- Reject minimized targets in the GDI fallback probe and capture path, and add
  automated Windows lifecycle tests for resize, minimize/restore, and
  cross-monitor moves against live windows.
