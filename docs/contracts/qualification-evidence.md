# Qualification evidence contract

UGA V1 promotion is evidence-driven. A status string in a checklist is not
sufficient evidence. `uga-qualify` maintains a versioned ledger containing all
required gates, a traceable source revision, evidence summaries, and SHA-256
digests of supporting artifacts.

A passed gate must reference at least one artifact beneath the evidence root.
Loading or displaying a ledger re-hashes every referenced file. A changed or
missing file invalidates verification. The repository-license gate additionally
requires a `LICENSE*` artifact. Placeholder revisions such as
`workspace-unversioned` can never yield a releasable manifest.
Every passed record also carries the full 40-character Git revision observed
when it was recorded. It must match the ledger revision; legacy or hand-written
records without that binding are rejected rather than silently promoted.
Package, capture, control, Recorder, dataset, model, and generalization gates
must additionally reference at least one JSON artifact whose own top-level or
enveloped `source_revision` matches the record. Hashing an older report while
recording it at a newer checkout is therefore insufficient.
The JSON must also be the expected typed report for that gate: build
qualification, Fixture qualification, Dataset Manifest, model-qualification
aggregate, or benchmark report as applicable. A generic hand-written JSON object that merely
copies the current revision is not promotion evidence. Duration and structure
minimums are enforced for the capture, Recorder, dataset, control-matrix, and
four-scenario benchmark gates.
The control gate uses an `uga.control_qualification` aggregate. It hash-binds a
passed current-revision Fixture report (focus loss, held-key neutralization,
emergency hotkey, and watchdog timeout all genuinely exercised) and a separate
`uga.uipi_qualification` report. The UIPI report is valid only when the real
Win32 integrity provider observes a higher-integrity, developer-owned target
and `FocusGuard` rejects the side-effect-free F24 key-up probe with
`integrity_incompatible`; same-integrity and simulated results fail closed.
The model gate requires a GPU-backed `uga.model_qualification` aggregate bound
to the Dataset Manifest. Motor, instruction, recovery, reasoning-gate, and
DAgger stages must each bind an artifact plus typed offline and closed-loop
metric reports. Ledger verification recursively re-hashes those source reports,
recomputes their declared finite metric thresholds, verifies each training
artifact and its inputs, and rejects a digest-only handwritten aggregate. A
deterministic motor feasibility checkpoint alone cannot promote V1.

The required gates cover automated tests, package construction and installation,
capture soak, supervised control hardware tests, ten-minute Recorder/Replay,
the licensed five-hour dataset milestone, model training, generalization
across the four developer-owned Fixture scenarios (V1 claim scope; real-game
generalization with licensed data is deferred to V2), and repository
licensing/governance.

Typical ledger operations:

```powershell
uga-qualify init runs/qualification-v1 --source-revision <commit>
uga-qualify record runs/qualification-v1 capture-soak passed `
  --evidence "30-minute fixture and game capture matrix passed" `
  --artifact capture/soak-report.json
uga-qualify status runs/qualification-v1
uga-qualify preflight runs/qualification-v1 --project-root . `
  --model-qualification runs/qualification-v1/models/model-qualification.json `
  --dataset-manifest data/datasets/v1/manifest.json `
  --dataset-root data/datasets/v1
uga-qualify manifest runs/qualification-v1 dist/<bundle> --version 1.0.0 `
  --preflight runs/qualification-v1/preflight.json --project-root .
```

The final command verifies evidence again before creating a release manifest.
`preflight` is read-only: it checks a clean Git worktree and HEAD alignment,
binds the qualification ledger, five-stage model aggregate, and Dataset Manifest by digest,
and checks repository licensing,
every recursively verified training-artifact binding, Dataset artifact hashes and explicit
distribution/commercial permissions, five-hour volume, the Train A/B/C plus
locked-test-game shape, GPU discovery, and every ledger status. It does not
execute tests or turn incomplete gates into passed gates.
Each development bundle also hashes `third-party-inventory.json`, generated from
the installed Python packages, locked Cargo graph, and locked npm UI build graph.
Known license declarations are necessary but do not replace human legal review.
The release build additionally runs the final `run_uga.ps1` launcher against the
completed bundle and emits an external `build-qualification.json`. That report is
bound to the full Git revision and hashes the final manifest, wheel, source
archive, native DLL, launcher, and dependency inventory. It records every build,
test, clean-install, installed-command, manifest, and launcher check separately.
It remains outside the bundle to avoid a self-referential manifest hash and is
the JSON evidence used for the automated-tests, package-build, and
package-install-smoke ledger records.
