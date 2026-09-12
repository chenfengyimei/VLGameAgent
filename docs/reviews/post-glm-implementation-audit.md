# Post-GLM implementation audit

Date: 2026-09-11  
Audit baseline: `60a51dc`  
Remediation commits: `2c02d58`, `df1561c`, `bb9582e`, `d04d8e4`, `0a962b7`
Continuation hardening: `9cee1f4` through `4541c92`

## Verdict

The GLM continuation delivered broad implementation coverage for UGA-001 through
UGA-075, and the repository is in substantially better engineering condition than
the handoff baseline. It has not completed UGA-075 release qualification.

The distinction is objective:

- The automated source gates pass: Ruff, strict mypy over `uga` and `apps`, 309
  pytest tests, 79 subtests, TypeScript typecheck/build, Cargo fmt, Clippy, tests,
  and release compilation.
- The opt-in owned-Fixture physical-input test passes at the current candidate
  revision. It proves the basic SendInput/PID ownership path, but does not replace
  the full supervised control fault matrix.
- Historical evidence includes a 30-minute owned Fixture WGC run, a 10-minute
  owned Fixture Recorder/Replay run, API/capture samples, and a four-scenario
  Fixture benchmark report with 100% recorded success.
- `runs/qualification-v1/qualification.json` and `preflight.json` do not exist.
  Consequently those reports have not been assembled into the required hashed,
  source-bound promotion ledger.
- The newest development corpus report contains about 0.006 hours, far below the
  required five hours, and is bound to an older source revision.
- No qualification-volume production training artifact is present; the current
  artifact is explicitly a development smoke result.
- The most recently rebuilt development bundle is internally hash-consistent and
  records source revision `321bb98...` with `releasable: false`. Later source
  hardening means it is again a diagnostic package rather than the final bundle.

Therefore the current state is: implementation substantially complete,
development verification passing, release qualification incomplete.

A current-revision development smoke additionally completed the full owned
Fixture corpus → Dataset Manifest → provenance-checked samples → deterministic
motor checkpoint → verified training artifact → 20-run four-scenario benchmark
path. It used only about 0.00694 train hours and three samples, so it proves
composition but deliberately does not satisfy the dataset or production-model
gate.

## Confirmed defects corrected during audit

1. The Windows bundle launcher depended on PowerShell command/module autoload for
   SHA-256 verification. A reduced PowerShell environment could not start a valid
   bundle. Hashing now uses the .NET cryptography API.
2. Live target selection matched only a window title and ignored the profile's
   executable allowlist. Target discovery now requires both, and every geometry
   refresh verifies the original process/window identity.
3. YAML strings such as `"false"` were coerced to true for safety, capability,
   control-confirmation, and model-enable flags. These inputs now require actual
   booleans. Non-finite action rates, confidence, sensitivity, and live CLI timing
   values are rejected.
4. VLM mode referenced an undeclared dashboard CLI option. The parser now exposes
   and bounds it.
5. Live Episode recording was not connected to the action-chunk controller, so
   action evidence could be absent. The recorder is now connected.
6. Live metrics reported zero captured frames, equated scheduled actions with
   executed actions, and forced an execution ratio of 1.0. They now use cumulative
   measured counters, and runtime failures finalize as failures.
7. An agent-task failure could bypass input neutralization and backend shutdown.
   Main-loop cleanup now attempts every safety operation and re-raises the original
   failure after evidence finalization.
8. Vision responses were unbounded and the history sampler could retain several
   gigabytes of raw frames. Response and history byte budgets are now enforced.
9. The loopback decision dashboard lacked Host validation and browser hardening.
   It now rejects DNS-rebinding-style Host values, emits restrictive headers, and
   closes safely even if startup is incomplete.
10. The bundled profile for the third-party online MMO `mumu-xianyu` claimed the
    environment was developer-owned and non-multiplayer. It is now classified as
    online/multiplayer with automation disabled, so it fails closed before window
    activation or input injection.

## Remaining risks and required continuation

### P0: required before UGA-075

1. Freeze an audited source commit. Do not create the qualification ledger while
   the worktree is changing.
2. Re-run Ruff, mypy, pytest, UI build, Cargo gates, package build, clean install,
   launcher smoke, and dependency inventory at that exact revision. Store logs as
   hashed evidence.
3. Run the supervised physical control matrix at the same revision: focus theft,
   held-key fault, integrity/UIPI rejection, emergency hotkey, watchdog, and
   neutralization. An operator must verify that no input reaches another window.
4. Repeat the required capture and Recorder matrices after the final runtime fixes.
   Historical reports are useful diagnostics but should not promote a newer binary.
5. Collect at least five hours of licensed, quality-reviewed owned Fixture data
   with Train A/B/C and a locked held-out Fixture game. Verify every Episode and
   the Dataset Manifest.
6. Train on a detected GPU and emit a verified training-artifact manifest binding
   the exact source revision, Dataset Manifest, samples, config, checkpoint,
   metrics, and license metadata. Run the staged offline and closed-loop metrics.
7. Re-run the four-scenario held-out benchmark using that artifact and record the
   required recovery/failure metrics, not only success rate.
8. Initialize `qualification.json` with the frozen revision, record every gate with
   artifacts, run preflight with the final dataset/training bindings, and require
   zero blockers.
9. Build a fresh bundle from the frozen revision, generate the qualified manifest,
   test it on a clean Windows account/machine, perform rollback, and verify the
   bundle tree. Only a manifest with `releasable: true` may be named UGA-075.

### P1: architectural hardening

- Increase native backend unit/contract coverage. The Rust workspace builds and
  its integration evidence is useful, but most platform behavior is currently
  proven by supervised reports rather than granular native tests.

Completed during continuation: the VLM planner now defaults to an
environment-neutral prompt and only enables Android quest-tracker heuristics via
an explicit Game Profile strategy. Generated Fixture qualification, corpus, and
benchmark reports carry clean source revisions; training is bound to the clean
HEAD and Dataset Manifest; and every passed ledger record must carry the same
full Git revision as its ledger.
Partial capture starts and Recorder setup now roll back their owned resources,
including private Episode staging trees. The Fixture launcher uses the actual
GUI-owner PID even from a Windows virtual environment, and Python/Rust Frame
contracts reject undersized packed strides and CPU buffers before memory access.

## Release decision rule

Do not infer release readiness from file count, commit count, a successful package
build, or historical Fixture reports. The sole promotion condition is a clean,
current-revision preflight plus a verified bundle manifest whose every required
gate is passed and whose `releasable` field is true.
