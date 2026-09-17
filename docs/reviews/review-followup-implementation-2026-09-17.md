# Review follow-up: execution safety and causal recording

Baseline: `a15d6d003dd95f59dec6ddd78fd5b930348fddc0`.
This is a focused remediation batch, not a V1 release qualification.

## Changes

- RV01: reject an execution frame or its validated reference when older than
  the execution freshness budget (default one second), including unchanged
  frame IDs. Recheck run, task, window, geometry and frame age before every
  queued primitive. A rejected primitive releases held input and flushes the
  remaining work.
- RV02: the session owns task generations. Observe fresh task evidence before
  validating a returned decision; cycle rebuilds advance the run epoch only.
  Re-reading the same frame cannot satisfy two-frame restored-task confirmation.
- RV03: repeated-action escape consumes the shared recovery budget, including
  when the budget is zero. Progress-control exemptions precede escape caching.
  A blocked continuous run stops instead of recreating the same blocked state.
- RV04: capture a new pre-action observation before scheduling. Receipts carry
  `pre_action_observation_id` and `pre_action_capture_ns`; the first primitive
  must follow that observation. Training inputs never use post-action effects.
  GUI proposals require all child receipts before any positive primitive label.
- RV05: incidental OCR overlap is not a completion predicate. A DONE result
  without configured `--goal-evidence` blocks as unverified; explicit evidence
  still requires the existing multi-frame/context checks.
- RV06: fatal authentication/quota failures escape the real grounded loop.
  Transient planner failures have a five-attempt budget; finite Retry-After is
  respected, and waits above five minutes stop instead of being shortened.
  Nested TaskGroup provider failures map to exit 78, which the PowerShell
  supervisor will not restart. Non-provider failures preserve their original
  exception behavior.
- RV07: finalize the receipt pump after scheduler neutralization even without
  another observation. Reserve receipt capacity at admission: over-capacity
  proposals fail before scheduling instead of silently overwriting evidence.
- Transport boundaries: deny HTTP redirects for image/credential requests,
  reject endpoint query/fragment ambiguity, redact quoted credential values
  and the configured API key, reject truncated/reasoning-only output, and
  propagate fatal verifier failures.
- Apply the exact Rust 1.97.1 formatting changes identified in the failed CI.

## Recording compatibility

Receipt envelopes add version `/2` fields; old Episodes are not rewritten.
`execution_observation_id` retains its historical POST-action meaning for
read compatibility. New `effect_observation_id` labels that role explicitly.
Only `pre_action_observation_id` may supply a training input.

Older recordings with only post-action observations remain readable, but are
unqualified for causal training export. Do not fabricate pre-action timestamps
or relabel effect observations to regain qualification. Legacy/offline fixture
producers that do not bind a real pre-action observation must be migrated
before their recordings can qualify. Lifetime-based qualified-duration
accounting is not redesigned in this batch and needs separate review before
qualification-volume data collection.

A pre-action observation is deliberately built from the actual execution frame,
without copying OCR fields from an older frame. The original inference
observation remains available separately for auditing the model decision.

## Verification

New tests live in `test_review_followup.py`, `test_causal_receipts.py`, and
`test_review_transport.py`. They exercise the real agent-loop/scheduler paths,
Recorder -> Replay -> Dataset round trips, and mocked transport boundaries.

The initial runtime negative suite reproduced eight failures on the baseline.
The local Linux/Python 3.13.5 run passes Ruff and strict mypy with the Windows
platform selected. The portable suite passes 623 tests plus 101 subtests;
one Windows-native discovery case is deselected locally after reproducing
its same Linux failure on the pristine baseline. No test is skipped or removed
in committed code; Windows CI runs that case normally. The Windows-only test
directory is not run on Linux. These local results are not Windows input,
GPU-training or actual model-service evidence.

Pinned Windows Python 3.11.9, complete pytest, Rust 1.97.1 fmt/clippy/tests/native
build and TypeScript checks must pass before this branch is published by the
review workflow. Consult the workflow run for the authoritative result rather
than interpreting this requirement as a completed run.

## Not closed by this batch

- Supervised hotkey/UIPI/held-input latency and device-loss/driver-hang tests.
- Full asynchronous recorder/backpressure redesign and long-duration soak.
- Game-specific semantic authorization for payments, agreements and other
  sensitive confirmation buttons; no new authority is granted here.
- A total multi-request inference deadline and calibrated visual-target
  tolerance policy beyond the physical execution freshness bound.
- Complete GUI semantic training export, qualified-duration redesign, formal
  five-stage GPU training, GLM-5.3-Flash runtime qualification and V1 promotion.

## Deployment and rollback

Use an isolated repair branch and a pull request; do not overwrite `main`.
Configure goal evidence for bounded tasks before expecting SUCCEEDED. Cloud
endpoints must be final HTTPS URLs without query/fragment/redirect hops.
Do not use newly qualified data with an older reader that ignores the causal
fields. Rollback should stop the agent, switch code as a unit, and keep existing
Episode directories immutable; it does not make old post-action labels valid.
