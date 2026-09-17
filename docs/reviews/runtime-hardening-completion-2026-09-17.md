# Runtime/data remediation: module delivery and acceptance boundary

Baseline: `c4af90632865b4d8e758d6eabc84bd410ada38aa` (merged PR #1).
Baseline tree: `0b67ee435b66572f3d188e4e1b1153ed992f65d5`.

This delivery implements runtime/data follow-ups from the prior review.
Modules are separate git commits. Code remediation, continuous integration,
and hardware/model release qualification are distinct claims. This document
**does not certify V1, zero defects, or completed neural training**.

## Delivered modules

| Module | Implemented behavior | Principal regressions |
| --- | --- | --- |
| Recording | Ordered queue bounded by frames and bytes; in-flight capacity reserved; encoding outside metadata lock; overflow and codec failure stop recording | `test_frame_queue.py`; strengthened `test_capture_hub.py` |
| Sensitive pages | Payments, agreements, credentials and deletion cues require operator handoff before model, rules, recovery or completion; no automatic agreement tick | `test_semantic_gate.py`; supervisor/strategy tests |
| Inference | Shared absolute deadline across model, repair and verifier; abandoned single-flight workers cannot be reused; nested socket budgets respect remaining time | `test_deadline.py`; real loop regressions |
| Model options | GLM-5.3 requires enabled thinking; optional fields cannot override model/input/output budget; launcher keeps legacy and GLM settings separate | mocked transport and capability tests |
| Causal data | Complete logical GUI/canonical receipt groups; no post-action training input; evidence coverage measured from capture to actual execution, not TTL | causal receipts and dataset round trips |
| GUI export | Recorded pre-action video frames, logical labels and explicit coordinate/image geometry; bounded, atomic new output, no source mutation | `test_gui_training_export.py` |
| Native | Serialized Python handle lifetime; buffer checks before copy; nonblocking command admission; bounded response/destruction; abandoned-worker cap | `test_native_lifecycle_bounds.py`; Rust FFI tests |
| Fixture production | Real causal receipts; separate movement/look/interact labels; reset/menu diagnostics excluded; canonical axis and sensitivity corrections | `test_fixture_evidence.py`; baseline runtime |
| Lifecycle | Shared public CLI parser; startup cannot clear early stop; status sees latch before slow cleanup; bounded finalization; fatal resource failures cannot auto-restart | `test_lifecycle_hardening.py`; shutdown tests |

## Recording invariants

Capture publication serializes recording admission, not codec work. The default
queue is 64 frames / 256 MiB, including the in-flight frame. A full queue is a
fatal evidence failure, not a silent frame drop. Observation, action, planner
and receipt metadata do not wait behind an encoder's metadata lock.

After input is disarmed, capture stops and recording drains with an explicit
bound. Drain failure leaves unpublished staging; it is not renamed as a valid
Episode. Video close and artifact publication use a separate 15-second live
budget. Timeout prohibits later publication and retains diagnostic staging.
A rename that already completed is not undone by deleting the finished artifact.
Python/native threads cannot be safely forcibly killed; an abandoned encoder
or driver requires the run to stop. This is not proof that every disk/GPU can
sustain any selected recording rate.

## Sensitive-page handoff

No model confidence, target-label spelling, deterministic rule source or broad
user goal grants permission to perform critical account/payment/agreement
operations. The shared guard applies to generic profiles too. The automatic
agreement checkbox rule and its proposal-time state update are removed.

The guard uses recognized OCR cues and explicit critical risk. It is not a
universal classifier of unreadable text, icon-only payment controls, unknown
languages or adversarial interfaces. Conservative false-positive handoffs are
possible. Do not disable the guard to improve an autoplay success metric.
Game rule inventories restrict which fast paths are available; they do not
constitute informed consent or bypass final input authorization.

## Absolute budgets and public entry points

The console and direct runner share one parser. This fixes the earlier public
entry missing newly added safety fields. Supported controls include:

```text
uga-agent run --profile PROFILE --watchdog-timeout-seconds 60 \
  --decision-timeout-seconds 90 --vlm-timeout-seconds 30
```

The watchdog measures control-plane activity. The decision deadline covers the
main call, schema repair and secondary verifier together, using wall monotonic
time rather than a per-request retry reset. A worker abandoned by timeout or
cancellation is poisoned and the run is invalidated, not retried on fresh
threads. Capture has its own bounded worker per source. Late model output
cannot regain input authority. No code claims to preempt an operating-system
input call that has already begun.

GLM-5.3/Flash rejects disabled thinking and unsupported reasoning effort. The
MuMu launcher chooses enabled thinking and a larger output budget for those
models, instead of the legacy no-thinking switch. Reference verified on
2026-09-17: <https://help.aliyun.com/en/model-studio/glm-zhipu>.
This is request validation, **not a live API qualification**. No paid model
request or personal screenshot upload occurs in the tests.

Fatal provider, recording and cleanup failures map to exit 78; the supervisor
does not restart them. Other retry paths have a total cap that is not reset by
a long healthy period. Startup model-readiness retries are also bounded.

## Causal evidence and derived data migration

An action's training input must be captured before its first physical
primitive. Every child primitive must have a matching successful terminal
receipt. Inference observations, execution-precondition observations and
post-action effects remain different fields. Partial clicks are not positive
logical labels. Completed fixture groups are removed from the producer's
working map; Episode provenance retains their immutable history.

Qualified duration is the union of pre-action capture to last-execution
intervals, clipped to Episode bounds. It is not unused action TTL, queue
horizons, idle video or total wall-clock run time. A point sample can qualify
while contributing zero duration. This metric measures demonstrated execution
evidence, not all useful unlabeled video.

Existing Episodes remain immutable. Rebuild derived manifests/exports that
previously counted lifetime-based coverage. Verification rejects claimed
coverage differing from the recordings. Do not rewrite timestamps or rename
post-action observations to obtain a qualified label. The release corpus tool
reports raw wall-clock hours separately and rejects a five-hour claim if actual
qualified training coverage is below five hours, retaining its diagnostic report.

## GUI export

```powershell
uga-dataset gui-export runs/episodes/EPISODE_ID --output runs/gui-training/EPISODE_ID
```

Output must be a new directory, outside the source Episode. `samples.jsonl`
contains receipt-qualified logical GUI actions. `images/` contains decoded
recorded pre-action video frames. Rows preserve client-normalized actions,
original geometry, decoded dimensions, and crop/resize mapping. MP4-derived
images are explicitly `recorded_video_lossy`, not original lossless pixels.
Resource limits cover frame count, dimensions, decoded selection and output
bytes. Missing video, mismatched timeline, partial actions and unsafe output
paths fail without fabricating examples or mutating source recordings.

This supplies supervised GUI examples, **not an implemented neural GUI
trainer**. Motor-only export filters canonical motor labels. OpenCUA must also
pass receipt qualification; it cannot fall back to proposal-only labels.

## Native and fixture behavior

Native initialization, response and destruction have outer bounds. Command
admission cannot block behind a full queue. Poisoned sessions fail immediately.
After two abandoned native workers the process refuses new sessions instead of
accumulating driver threads; restart the process, not just a driver object.
Python serializes start/capture/stop, clears ownership before destruction and
checks pointer/stride/dimensions before copying returned frame memory.

Fixture records declare logical intents before input so even an aborted,
reset-only run cannot fall back to diagnostic primitives as training labels.
Only successful complete child receipts qualify those intents. Reset/menu
operations are not movement children. Canonical +Y means W/forward rather than
screen-pixel down; the camera label matches actual dx / configured sensitivity.

## Verification

Current portable supplementary run: Linux / Python 3.13.5, **658 passed,
130 subtests passed**, no deselected tests. The invocation excludes only the
Windows-only directory. Ruff passes; strict mypy (`--platform win32`) checks
190 source files. These are not substitutes for Windows execution.

```text
ruff check .
mypy --platform win32 uga apps
python -m pytest -q --ignore=tests/windows
```

Before the repair branch is published, a Windows workflow must validate pinned
Python 3.11.9 and locked dependencies; Rust 1.97.1 fmt/clippy/test/release DLL;
full pytest with that DLL hash-pinned; lifecycle and owned Fixture headless
smoke; benchmark config and dependency inventory; wheel/sdist; Node 24.15.0
npm installation, type check and build. Consult the final PR and run logs for
actual outcomes; this paragraph lists requirements, not a pre-recorded PASS.
Pinned rustfmt-only changes, if needed, get a separate commit. Publication
fast-forwards only the repair branch using the exact verified tree. Temporary
transport/validation workflows are not included in the final PR. Main is not
automatically merged.

## Not claimed by this delivery

| Gate | Status | Evidence still required |
| --- | --- | --- |
| Actual MuMu/WGC/DXGI behavior | NOT_RUN | Supervised recordings at delivered SHA |
| Emergency latency, held keys, UIPI and focus theft | NOT_RUN | Opt-in physical input fault injection |
| Multi-monitor/DPI/device-loss/stuck-driver matrix | NOT_RUN | Hardware and target-context results |
| 30-minute / 2-hour / 8-hour soak | NOT_RUN | Memory, queues, threads, handles, capture gaps and stop latency |
| GLM-5.3-Flash live API | NOT_RUN | Authorized bounded-cost endpoint qualification |
| Five-stage GPU training and held-out evaluation | NOT_IMPLEMENTED_AND_NOT_RUN | Real neural backend, licensed data/checkpoints and training logs; deterministic motor baseline is not that |
| Five-hour qualified corpus | NOT_COLLECTED | Actual evidence volume, licenses and locked splits |
| Formal V1 promotion | BLOCKED | All source-bound checklist gates plus clean installation/rollback |

Stop the agent before updating. Preserve incomplete staging for diagnosis; never
manually rename it to a published Episode. Start with owned Fixture tests and
physical input disabled. Roll back a coherent source version, not isolated
recorder/reader files, and never revive invalid post-action training labels.
