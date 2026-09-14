# Final direct-use review

Date: 2026-09-15  
Candidate: `79742a46ec8c68993b1122db91a3d88f2d688c93`

## Outcome

The development bundle is ready for direct supervised use. Capture, grounded
single-action planning, OCR fusion, fresh-frame rejection, action-effect and
two-frame goal verification, bounded recovery, recording/replay, and the
loopback-only live dashboard are implemented and covered by automated and live
evidence. The bundle deliberately remains `releasable: false`; this review does
not substitute shorter evidence for the unfinished UGA-075 release gates.

## Final checks

- Ruff passed and strict mypy passed across 174 source files.
- Pytest passed 412 tests plus 89 subtests; the separate opt-in physical-input
  test remained skipped by design.
- TypeScript typecheck/build and locked Rust tests/release build passed.
- The isolated wheel install, every shipped CLI, bundle manifest and bundled
  launcher passed the 14-check build qualification.
- A source-bound 18-second owned-Fixture run captured 490 frames, scheduled and
  executed 36/36 physical events, recorded three canonical actions, reached the
  visual target, and produced an accepted 100-quality Episode. Focus-loss
  rejection, held-key neutralization, emergency hotkey and watchdog exercises
  all passed.
- A long-run defect found during corpus collection was fixed: if an OS/capture
  stall consumes the scheduling lead, the cycle and all later cycles are now
  shifted into the future. Expired actions are never backfilled or burst-fired;
  the rebase count and cumulative shift are recorded in qualification output.

## Visual-loop evidence retained from the unchanged Agent runtime

At `d6da6d4d945a84a29f92e208a42e5eeac13cac32`, the local
`qwen3-vl-4b-instruct` path passed the 200-sample offline grounding corpus, the
20-goal-by-5-run MuMu matrix plus injected loop, and a 30-minute MuMu soak. The
matrix had 100/100 task successes, zero wrong-window/target/critical/stale or
non-action physical inputs, and detected the injected loop with bounded
recovery. The soak captured 14,671 frames with p95/max gaps below 350/500 ms,
made no physical input for the observation-only goal, and terminated as
`failure/timeout` rather than false success. Commit `79742a4` changes only the
owned-Fixture qualification scheduler and its tests; the Agent/VLM runtime is
byte-for-byte unchanged from that evidence revision.

A new MuMu attempt on `79742a4` stopped before capture/input because an elevated
Task Manager window held foreground focus. The guard correctly refused to
bypass Windows UIPI. Minimize elevated or always-on-top windows before a live
run; see the Chinese MuMu guide.

## Evidence and package

- Bundle: `dist/uga-0.1.0-dev-79742a4-windows-x64`
- Build evidence: `runs/qualification-final-79742a4/build/build-qualification.json`
- Current Fixture smoke: `runs/qualification-final-79742a4/fixture-smoke/report.json`
- MuMu matrix: `runs/qualification-vlm/live-final-d6da6d4/report.json`
- MuMu soak: `runs/qualification-vlm/soak-d6da6d4/episodes/`
- Offline grounding: `runs/qualification-vlm/offline-d6da6d4/report.json`

## Explicit release boundary

The following are not claimed: a completed five-hour reviewed Dataset Manifest,
real five-stage GPU training/evaluation, a trained-policy four-scenario release
benchmark, or the supervised elevated-owned-target UIPI probe. Consequently the
final UGA-075 ledger is not releasable and no remote push is authorized by this
review. These are release-qualification omissions, not hidden runtime passes.
