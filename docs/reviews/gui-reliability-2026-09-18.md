# GUI reliability follow-up (2026-09-18)

Base: `6dbaa640710874a9fd8b2bfacb345c6ae13e8f65`, after PR #5.
This batch fixes a Windows CI timing assumption and closes observable GUI
validation gaps. It is not an online-model accuracy or release qualification.

## Capture cadence

The previous test assumed three fallback frames would be published within
650ms of task creation. That combines source startup, worker scheduling, pixel
copy and host load with the algorithm under test. It failed twice in PR #5 CI.
The production capture scheduling is unchanged.

Six virtual-clock tests execute the real fallback coroutine and real publication
path. They check exact start-to-start timing, copy/publication overhead, delayed
wakeups, transient failures, primary recovery and the default quiet threshold.
A mutation that timestamps the last attempt at copy completion causes five of
these tests to fail. Real-thread coverage still requires three published frames
within a bounded liveness window and validates actual timestamp accounting;
it no longer claims a host-performance guarantee from a 650ms startup race.

## Last-moment target evidence

The final fresh perception is checked after the optional verifier, before
submission. A newly disabled control, conflicting local OCR or disappearance
of a previously matched target cannot be overridden by unchanged sampled pixels.
Disabled state is checked at the actual landing point independent of the model's
chosen label and bounding-box size. Final rejection, not the earlier approval,
is sent to next-step model feedback. Sensitive-page detection also revokes and
neutralizes input before returning WAIT.

Three regression cases failed on the base implementation and now pass through
the real main loop and DryRun backend. No threshold or input-ownership check
was disabled to make them pass.

## Evidence for declared postconditions

Explicit `text_appears`, `text_disappears`, `target_changes` and `scene_changes`
use a dedicated tracker, separate from legacy generic stabilization:

- Text appearance requires changed pixels at the new OCR region, no prior
  occurrence (including uncertain OCR), and two observations at a consistent
  location. Ambiguous multiple new occurrences do not qualify.
- Disappearance requires pixels to change at the original text regions plus
  nonempty current OCR. A low-confidence remaining occurrence is uncertainty,
  not absence. OCR dropout or relabeling on unchanged pixels cannot succeed.
- Target state uses the control under the actual pointer, not a remote control
  with the same name. Pixel-only effects require stable target pixels; a
  continuing animation cannot qualify as a settled target change.
- Scene transitions require semantic and visual change. Pure numeric counters
  do not qualify; confirming observations must agree on the scene evidence.
- Snapshot identity/time must match its pixel frame. The minimum effect delay
  uses acquisition time, not delayed OCR completion. The absolute timeout is
  never extended, and late confirmation cannot turn an expired action into
  success.

An explicit-effect action retains one immutable baseline, capped at 64 MiB.
Already immutable buffers are shared; mutable buffers are copied once. Larger
baselines cannot qualify an effect and fall through to the existing bounded
failure handling. Normal game frames should stay well below this limit.
Entering a sensitive page invalidates and records the pending trace instead of
silently dropping it. Generic effects without an explicit predicate preserve
their existing conservative path.

Seven initial evidence regressions failed on the prior implementation. Tests
also cover mutable capture buffers, same-location confirmation, low-confidence
OCR, late frames, animation, numeric counters and oversized baselines.

## Compatibility and limits

No CLI, model, credential, coordinate-space or Episode schema migration is
required. Qwen3-VL-4B and GLM-4.6V continue through the existing common protocol.
Stricter checks may correctly increase re-observation/abstention when the UI is
uncertain. They must not be disabled merely to increase the action count.

The evidence tracker observes correlated visual change; it cannot prove the
agent caused every transition. OCR errors and image sampling have residual
limitations. No paid API, real game or physical input was exercised here.
Live model accuracy, hardware timing and long-run gameplay remain separate
qualification work. Exact test results and commit identity belong to the PR
and its CI artifacts, not an assumed future green result in this document.

Rollback: stop the agent, then revert this batch or switch to a consistent prior
revision. Do not alter original Episodes, reset safety latches in a live session
or selectively mix old and new supervision modules.
