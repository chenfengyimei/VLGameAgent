# Runtime follow-up: module implementation, migration and acceptance

Base: `c4af90632865b4d8e758d6eabc84bd410ada38aa` (merged PR #1), tree
`0b67ee435b66572f3d188e4e1b1153ed992f65d5`.

This batch implements the software follow-ups below in individual module
commits. It does not claim formal V1 promotion, real-desktop qualification,
paid-provider accuracy, a licensed training corpus or GPU training results.
The existing deterministic CPU trainer remains a development baseline.

## Module ledger

| Module | Implemented contract | Regression tests |
| --- | --- | --- |
| Ordered asynchronous recording | `recording/channel.py`, CaptureHub, EpisodeWriter: atomic entry/byte reservations including in-flight work; FIFO publication order; explicit saturation/failure; bounded codec shutdown; incomplete staging never published | `test_async_recording.py`: 500-record accounting, slow disk, buffer bounds, drain failure, metadata-lock independence, ordered capture, video timestamp scaling |
| Total decision budget | One monotonic deadline and max-four HTTP-request budget across inference, repair, schema fallback, fresh perception and verifier; single-flight daemon workers retire on timeout/cancellation | `test_call_budget.py`: nested deadlines, request cap, actual-loop late model and cancellation |
| Sensitive-page suspension | Pre-inference and final-action checks for credentials, identity, payments, legal consent and destructive confirmations; no model/rule/goal-string authorization; remove auto-agreement click and speculative state | `test_sensitive_page.py`: overlays, split OCR, ordinary rewards remain usable, no provider or input on sensitive page |
| Logical GUI and mixed datasets | Store a GuiAction parent and physical children; complete, causal receipts required; export one logical label instead of mouse-down fragments; preserve mixed canonical/GUI samples | `test_gui_training_export.py`: nine GUI action kinds, partial/rejected groups, mixed episodes, conflicting capture timestamps |
| Qualified active duration | Union first-to-last actual executed primitive intervals, clipped to Episode bounds; valid instantaneous labels contribute zero active duration; never count authorization TTL | TTL inflation regression; manifests are reverified against recomputed evidence |
| Fixture production | Capture and record pre-action observations at each due reset/menu/look/motor/interaction group; only movement becomes a canonical motor label; finalize after neutralization and receipt drain | `test_fixture_causal_producer.py`: real dry scheduler/recorder/replay/processor chain, stale-frame rejection |
| Native ownership and deadlines | Python serialized handle ownership; bounds checked before native memory copy; Rust nonblocking command admission, bounded init/healthy-close waits, poison-state rejection | `test_native_lifecycle_bounds.py`; Rust full-queue, teardown and timeout tests |
| Model request and launcher policy | Protected extra-body fields, bounded images, conservative GLM enabled-thinking operational profile; finite process-lifetime restart budget; rotated child logs | `test_model_policy.py` and existing provider/launcher tests |
| Final visual and effect context | Re-read after slow verification; updated OCR required for dynamic-target waiver; primitive-dispatch recheck; effects cannot cross task/window/geometry boundaries; frozen frame never extends deadline | `test_final_visual_guard.py` |
| Capture and diagnostic lifecycle | Retire a stalled capture source once, keep fallback viable, no default-executor consumer wait; bound/rotate optional diagnostic writes while propagating durable Episode sink errors | `test_capture_deadlines.py`, journal slow-disk and rotation tests |
| GUI SDK boundary | Reject bool/non-finite coordinates/confidence, invalid/repeated virtual keys, invalid scroll range and too-short long-click lifetimes before partial input | `test_gui_sdk_boundaries.py` |

## Compatibility and operational choices

1. Sensitive pages pause automation until the owner handles them. OCR policy is
   conservative, not a complete privacy guarantee. Explicitly requested local
   recordings can contain sensitive screens; review their retention/sharing.
2. `--decision-timeout-seconds` defaults to 60 and
   `--perception-timeout-seconds` to 10. Repairs/fallback share the first
   deadline. At most four HTTP requests are admitted per decision. A timed-out
   worker is not reused, and its late result cannot authorize input.
3. Capture source calls have a four-second default outer bound. Native capture
   timeouts are integer milliseconds in [0,1000]. Initialization, response grace
   and teardown deadlines are separate. A driver thread is not force-killed:
   owned state stays alive until it exits. Bounded caller shutdown does not
   promise kernel-resource reclamation under a broken driver.
4. Recording defaults to 32 frames / 256 MiB including in-flight work. Overflow
   fails explicitly instead of replacing accepted evidence. Inspect
   `CaptureHub.recording_stats()` and `recording_complete`. Failed staging stays
   private; do not distribute it as a completed Episode.
5. Diagnostic JSONL has a current file and one backup, five MiB each by default.
   Its bounded disk worker cannot block the runtime's metadata lock. Diagnostic
   failure is visible separately; durable Episode sink failures still propagate.
6. `uga-dataset gui-export EPISODE... --output samples.jsonl` exports logical
   action fields with causal input observations and immutable source video refs.
   It does not claim to have extracted image tensors. Metadata-only contract
   fixtures explicitly emit `source_video: null`; they are not an image corpus.
7. Recorded Episode duration, causal label count, and measured active execution
   time are different quantities. Rebuild/reverify old derived manifests whose
   qualified duration used TTL. Keep source Episodes immutable. Five wall-clock
   hours do not imply five qualified active hours; fixture corpus promotion
   checks the actual measured value and retains artifacts when volume is short.
8. GUI long-click lifetime must exceed its 700ms hold so release is schedulable.
   Key chords may not contain duplicate IDs or invalid virtual-key codes.
9. `configs/models/glm-5.3-flash.yaml` is a repository request-policy record,
   **not live provider qualification**. Enabled thinking, a 1024-token minimum
   output budget and the launcher's 4096-token default are conservative local
   engineering choices based on the handoff, not claims of official API limits.
   Official availability/current behavior could not be independently confirmed
   in this run; no paid API calls were made and `live_qualification` is NOT_RUN.
10. Supervisor healthy intervals reset delay only, not total restart count.
    Fatal configuration/provider exit 78 remains non-restartable.

## Validation and provenance

The workspace was restored after a sandbox reset. The reconstructed code was
re-tested rather than inheriting earlier pass claims. Portable Linux/Python
3.13.5 validation: **671 passed, 1 deselected, 147 subtests passed**; Ruff passed;
strict mypy with `--platform win32` checked **188 source files**.

The local command excludes Windows-only files and deselects one baseline
Windows-native DLL discovery assertion. Submitted code adds no skip for this;
the full Windows validation must execute it with the actual built DLL.

Windows acceptance commands (Python 3.11.9, Rust 1.97.1, Node 24.15.0):

```powershell
python -m pip install --require-hashes -r requirements-lock.txt
python -m pip install --no-deps --no-build-isolation -e .
ruff check .
mypy uga apps
cd native
cargo fmt --all --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cargo build -p uga-capture --release --locked
cd ..
$env:UGA_NATIVE_CAPTURE_DLL = (Resolve-Path native/target/release/uga_capture.dll).Path
$env:UGA_NATIVE_CAPTURE_SHA256 = (Get-FileHash $env:UGA_NATIVE_CAPTURE_DLL).Hash.ToLowerInvariant()
pytest -q -ra
python -m apps.agent
python -m apps.example_game --headless-smoke
python -m apps.benchmark validate-config configs/benchmarks/uga-bench-smoke.yaml
python -m apps.dependency_inventory --require-known --output build/dependency-inventory.json
python -m build --no-isolation
npm ci --include=dev
npm run typecheck
npm run build
```

The final PR acceptance comment must record the actual remote commit series,
final tree hash, workflow IDs, failures/fixes, passed counts and skip reasons
only after the Windows runs finish. Temporary transport workflows are not part
of the repair PR. Main is not overwritten or automatically merged.

## External qualification remains open

- Supervised actual keyboard/mouse input, UIPI, focus theft, second-monitor/DPI,
  emergency-hotkey P99 and held-key fault injection: no live-desktop evidence.
- WGC/DXGI/GDI device-loss, GPU/driver resource recovery and 30-minute/2-hour/
  8-hour soaks: no hardware measurement in this implementation run.
- Licensed quality-reviewed multi-game image corpus, sufficient qualified
  active duration, held-out games and leakage review: actual data required.
- Five-stage GPU motor/instruction/recovery/reasoning/DAgger training and
  independently measured artifacts: NOT_RUN, not replaced by the CPU baseline.
- Authorized live GLM API compatibility, latency, cost and grounded-loop success:
  NOT_RUN. Offline contract tests are not provider/model qualification.
- Formal V1 promotion remains blocked until every applicable existing release
  gate has evidence for the exact promoted revision.

## Rollback

Revert modules with dependent contracts and regressions reviewed together.
Never restore automatic legal consent, relabel effect frames as pre-action
inputs, or recover nominal corpus volume by counting TTL. Keep source Episodes
immutable and regenerate derivatives with provenance. A code revert does not
make historical release evidence valid for a different tree.
