# VLGameAgent / UGA V1.1 — GLM-5.3 continuation plan

Updated: 2026-09-09  
Repository: `D:\xm\VLGameAgent`  
Remote: `https://github.com/chenfengyimei/VLGameAgent.git`  
Branch: `main`  
Handoff baseline: `5d979650736884d7ad811ca91e90816c1538a4b4`

## 1. Mission and authority

Continue the UGA V1.1 design through truthful UGA-075 qualification. The user
authorizes visible Fixture World runs, physical input, long-running tests, local
training, and other in-scope implementation work. The user requires one Git
commit after every completed modification batch. Do not push until every UGA-075
gate has genuinely passed; after that, the user authorizes the final push.

The complete source design is stored at:

`C:\Users\cy\.codex\attachments\ccad1fa8-a7fc-409a-bc19-86254b04b2c9\pasted-text.txt`

Read that file, `ARCHITECTURE.md`, `SECURITY.md`, `RELEASE_CHECKLIST.md`,
`docs/status/uga-v1-status.md`, and
`docs/contracts/qualification-evidence.md` before changing code.

## 2. Non-negotiable rules

1. Never call an OS input backend from Planner, GUI, policy, or tests. Physical
   input must pass through `InputExecutor`, an exact target identity, a live
   lease, the focus/integrity guard, watchdog, and emergency stop.
2. Never claim a gate from code presence alone. A gate passes only with current,
   content-hashed evidence from the final frozen Git revision.
3. Never reuse an old corpus, checkpoint, benchmark, soak report, or release
   report as final evidence unless its embedded source revision equals the final
   HEAD and all transitive hashes verify.
4. Never silently weaken UGA-075 from a real trained VLM policy to the
   deterministic Fixture policy. If hardware or model rights are insufficient,
   report the model gate as blocked and ask the user to choose a remote training
   target or explicitly revise the release scope.
5. Never invent a repository license. The user's broad authorization is not an
   SPDX license selection. Obtain an exact choice such as MIT or Apache-2.0
   before the release freeze.
6. Keep generated evidence under ignored `runs/qualification-v1/` and release
   output under ignored `dist/`. Do not commit large videos, datasets, models,
   virtual environments, or secrets.
7. Before each modification batch: inspect `git status`. After the batch: run
   proportionate tests, review `git diff --check`, commit, and confirm the tree
   is clean. Preserve unrelated user changes.
8. Do not push intermediate commits. Push `main` and the release tag only after
   the final release manifest reports `releasable: true` and verifies exactly.

## 3. Current verified state

All UGA-001 through UGA-075 implementation surfaces exist. This means the
contracts, runtime, Windows adapters, recorder/replay, baseline agent, dataset
pipeline, deterministic training path, benchmark, dashboard, and release tools
are represented in code. It does **not** mean UGA-075 is qualified.

At handoff:

- `git status --short` is empty at commit `5d97965`.
- No intermediate commit from this continuation was pushed. This clone has no
  local `origin/main` tracking ref, so fetch and inspect the remote explicitly
  before the eventual final push; do not infer remote state from local history.
- Ruff passes.
- strict mypy passes for `uga` and `apps`.
- pytest passes: `131 passed, 1 skipped`; the skipped test is the deliberately
  opt-in Windows physical-input test.
- Recent security hardening commits are:
  - `fa1139e` — neutralize input across authority loss;
  - `ffe405d` — bind training and benchmark evidence;
  - `407fb95` — bound local artifact resource consumption;
  - `7f5aab3` — authenticate Dashboard requests and native capture loading;
  - `5d97965` — bind promotion to immutable preflight evidence and the exact
    release bundle tree.
- A repository security scan at `8de4ab9` reported 15 findings. Findings across
  physical-input lifetime, fixture identity, Episode/training/benchmark
  provenance, resource bounds, Dashboard, native loading, and promotion have
  since been repaired. Do not mark the scan gate passed from this statement;
  run a new complete scan at the final code revision.
- The clearly outstanding scan item is mutable CI/build inputs. See section 5.
- Historical diagnostic evidence exists under `runs/qualification-v1/`,
  including WGC 30-minute and Recorder 10-minute reports plus short four-scenario
  corpora. These predate the handoff HEAD and are not final release evidence.

Host snapshot at handoff:

- GPU: NVIDIA GeForce RTX 5070 Laptop GPU, 8151 MiB VRAM, driver 596.13.
- Node: 24.15.0.
- Rust: 1.97.1.
- bundled workspace Python: 3.12.14; project compatibility target is Python
  3.11+, and CI currently targets the floating `3.11` label.
- `torch`, `transformers`, `peft`, and `accelerate` are not installed in the
  bundled Python runtime.

The design recommends at least 16 GiB VRAM for development and a multi-GPU
server for full 4B/8B VLM policy training. This 8 GiB laptop can run the Fixture
policy, inference/ablation, and possibly carefully configured LoRA, but it must
not be treated as proof that full Qwen3-VL-4B training was performed.

## 4. Definition of done

UGA-075 is complete only when all of the following are simultaneously true on
one final, clean, committed source revision:

- all automated Python, TypeScript, Rust, contract, schema, replay, and Windows
  tests pass;
- the final security scan has no unresolved reportable finding, or each accepted
  residual risk has explicit owner approval and tracked rationale;
- every gate in `RELEASE_CHECKLIST.md` has current hashed evidence;
- a minimum five-hour reviewed training corpus and locked held-out game exist,
  with verified Episode contents and distribution/commercial rights;
- the required model stages have real artifact manifests, offline metrics,
  latency results, and closed-loop results;
- Train A/B/C and held-out Game D benchmark results are complete and bound to
  the verified training artifact;
- the repository has an owner-selected license and reviewed third-party notices;
- clean-machine installation, launcher, rollback, and Windows native capture
  tests pass;
- `uga-qualify preflight` reports no blockers;
- the qualification ledger has all ten required gates passed;
- the qualified `release-manifest.json` reports `releasable: true` and
  `uga-release-manifest <bundle> --verify-existing` succeeds;
- final source revision, ledger, preflight, training artifact, Dataset Manifest,
  bundle, Git tag, and pushed commit all identify the same release.

## 5. Ordered continuation work

### Batch A — Pin the CI and build supply chain

This is the next code batch. Fix `.github/workflows/ci.yml`,
`scripts/setup_windows.ps1`, `scripts/build_release.ps1`, `pyproject.toml`, and
dependency lock inputs.

Required outcomes:

- set workflow-level `permissions: contents: read`;
- set checkout `persist-credentials: false`;
- pin GitHub Actions to immutable commit SHAs, not tags;
- pin exact Python, Node, and Rust patch versions;
- add a full transitive Python hash lock and install it with
  `pip --require-hashes`;
- install this project with `--no-deps --no-build-isolation` after the lock;
- build with `python -m build --no-isolation`;
- use `--locked` for Cargo build, test, and clippy operations;
- remove the unbounded `pip install --upgrade pip` from setup;
- keep `npm ci` and the committed `package-lock.json` as the UI input.

Previously resolved action-SHA candidates were:

- `actions/checkout`: `11d5960a326750d5838078e36cf38b85af677262`;
- `actions/setup-node`: `49933ea5288caeca8642d1e84afbd3f7d6820020`;
- `actions/setup-python`: `a26af69be951a213d495a4c3e4e4022e16d87065`;
- `dtolnay/rust-toolchain`: `6bed0761d98439e5a578e2877258200ad565ba87`.

Verify these are valid upstream commits before editing. Add tests or a repository
policy check that rejects mutable `uses:` refs and unhashed Python release
installs. Commit this batch as a single reviewed change, for example
`build(ci): pin release toolchain inputs`.

### Batch B — Re-scan and close residual security findings

Run a fresh complete repository security scan after Batch A. Reproduce each new
candidate before changing code. For every validated finding:

1. record source-to-sink evidence;
2. add a failing regression test;
3. implement the smallest fail-closed fix;
4. run focused and full tests;
5. obtain an independent post-patch review;
6. commit the completed finding or coherent finding group.

Explicitly revisit manifest authenticity. The bundle now detects any file-set or
content mutation, but a manifest and bundle supplied together still need an
external trust anchor. Prefer an owner-controlled signed release/tag or an
out-of-band manifest digest. Do not create signing keys or claim authenticity
without the owner's key decision.

### Batch C — Complete Windows and packaging qualification tooling

Build from a clean committed tree and exercise both native WGC and DXGI. Add only
missing automation or tests discovered by the matrix; commit each coherent fix.

The formal matrix must cover:

- DX11, DX12, Vulkan, and OpenGL targets where authorized and available;
- windowed, borderless, resize, minimize/restore, alt-tab, multi-monitor, and
  150% DPI;
- device/access loss and target restart/HWND reuse;
- focus theft, lease expiry, pause, process failure, watchdog timeout, and
  emergency hotkey while a key/button is held;
- exact neutralization and latency evidence;
- a ten-minute Recorder Episode with complete checksums and successful Replay;
- clean Windows installation, launcher integrity failure, rollback, and uninstall
  or removal behavior.

The developer-owned Fixture World is safe for automation, but it does not by
itself prove all four graphics APIs. Use only games/test programs the user owns
or has authorized. Keep a supervising operator present for physical input.

### Release freeze — choose license, commit, then stop changing tracked files

Ask the owner to select the exact code license before the freeze. Add the chosen
`LICENSE` and any required metadata/notice updates, validate third-party license
records, and commit. Finish every remaining code/doc fix and commit it. Record
the resulting full HEAD as `$releaseRevision`.

From this point onward, generate all formal evidence at that exact HEAD. If any
tracked file changes, make a new commit, choose the new HEAD, and regenerate all
revision-bound corpus, model, benchmark, ledger, and preflight evidence. Do not
edit generated JSON to make a gate pass.

### Gate D — Collect the formal five-hour corpus

Run the visible, supervised Fixture corpus only after the release freeze:

```powershell
$python = "C:\Users\cy\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$env:PYTHONPATH = "$(Resolve-Path .\.tooling);$(Resolve-Path .)"
& $python -m apps.qualification corpus `
  --project-root . `
  --output-root runs/qualification-v1/final-corpus `
  --train-duration-seconds 6000 `
  --test-duration-seconds 600 `
  --target-fps 3 `
  --backend auto `
  --allow-physical-input
```

This schedules three 6000-second training scenarios plus a 600-second held-out
scenario. Supervise it. On completion, verify `corpus-report.json`, every Episode
checksum, the Dataset Manifest, split isolation, license fields, actual duration,
and disk/resource limits. Preserve failure logs; do not merge partial runs into a
passing manifest without review.

The four Fixture profiles are useful deterministic Train A/B/C and Test D
proxies. If UGA V1 is meant to claim real cross-game generalization, they are not
sufficient: collect equivalent licensed data from three real training games and
one locked real held-out game, using adapters and rights records. Obtain the
owner's explicit scope decision before passing the generalization gate.

### Gate E — Train and verify models

The deterministic motor-policy path can be run locally:

```powershell
$releaseRevision = (git rev-parse HEAD).Trim()
& $python -m apps.training motor `
  --samples runs/qualification-v1/final-corpus/motor-samples.jsonl `
  --dataset-manifest runs/qualification-v1/final-corpus/dataset-manifest.json `
  --dataset-root runs/qualification-v1/final-corpus/episodes `
  --config configs/training/fixture_motor_bc.yaml `
  --output runs/qualification-v1/final-training/motor `
  --policy-version "uga-fixture-motor-$releaseRevision" `
  --source-revision $releaseRevision `
  --base-model-license project-owner-controlled
& $python -m apps.training verify `
  runs/qualification-v1/final-training/motor/training-artifact.json
```

This is an integration baseline, not proof of Qwen3-VL-4B training. For the V1
model claim, create a separate isolated, hash-locked training environment on
adequate hardware; verify the exact Qwen model card/license; implement or finish
the Qwen3-VL backbone, Action Head/temporal decoder, LoRA or distributed VeOmni
recipe as required; then produce real artifacts for motor, instruction,
recovery/DAgger, and reasoning-gate stages. Each stage must bind its base model,
dataset, config, samples, source revision, checkpoint, metrics, and licenses.

Required evaluation includes movement/camera/button/confidence metrics, latency,
navigation success, no-progress rate, camera smoothness, closed-loop recovery,
and held-out results. If no adequate GPU is available, leave `model-training`
blocked rather than labeling the Fixture decoder as the production VLM.

### Gate F — Run the bound held-out benchmark

For the deterministic integration benchmark:

```powershell
& $python -m apps.benchmark fixture `
  --config configs/benchmarks/uga-bench-fixture.yaml `
  --artifact runs/qualification-v1/final-training/motor/training-artifact.json `
  --output runs/qualification-v1/final-benchmark/runs.jsonl `
  --report runs/qualification-v1/final-benchmark/report.json
```

Do not use `--rule-baseline` for the model qualification gate. Confirm the report
contains exactly the configured task cohort and repetitions, binds the verified
training-artifact digest, and keeps the held-out game out of training. Repeat the
equivalent benchmark with the actual production VLM and, if required by scope,
the four authorized real games.

### Gate G — Assemble evidence, preflight, and release

Create a new evidence root for the frozen revision. Copy or generate concise
evidence files under it; keep every ledger artifact path relative to that root.
Initialize the ledger and record exactly these ten gates:

- `automated-tests`;
- `package-build`;
- `package-install-smoke`;
- `capture-soak`;
- `control-hardware`;
- `recorder-10min`;
- `dataset-5h`;
- `model-training`;
- `generalization-bench`;
- `repository-license`.

Every passed gate requires at least one hashed artifact. The license gate must
include an evidence file whose basename starts with `LICENSE`. Use
`uga-qualify status` after every record and never edit `qualification.json`
manually.

Then run:

```powershell
& $python -m apps.qualification preflight runs/qualification-v1/final `
  --project-root . `
  --training-artifact runs/qualification-v1/final-training/motor/training-artifact.json `
  --dataset-manifest runs/qualification-v1/final-corpus/dataset-manifest.json `
  --dataset-root runs/qualification-v1/final-corpus/episodes `
  --output runs/qualification-v1/final/preflight.json
```

Preflight must have an empty `blockers` array. It now binds and revalidates the
clean HEAD, ledger, licenses, training artifact, Dataset Manifest, Episode tree,
five-hour threshold, Train A/B/C, and locked test game.

Build the development bundle from the same clean HEAD:

```powershell
& .\scripts\build_release.ps1 `
  -Python $python `
  -BundleName uga-1.0.0-windows-x64
```

Promote it with the verified preflight:

```powershell
& $python -m apps.qualification manifest `
  runs/qualification-v1/final `
  dist/uga-1.0.0-windows-x64 `
  --version 1.0.0 `
  --preflight runs/qualification-v1/final/preflight.json `
  --project-root .
& $python -m apps.release_manifest `
  dist/uga-1.0.0-windows-x64 `
  --verify-existing
```

The qualified manifest must contain the exact bundle file set, the bundled
ledger and preflight, all passed gates, the final source revision, and
`releasable: true`. Test that adding, deleting, or changing any bundle file makes
verification and `run_uga.ps1` fail before native code or the agent starts.

### Gate H — Final review, tag, and push

Before pushing:

1. rerun the complete test matrix on the frozen revision;
2. run the final repository security scan;
3. verify release checksums/signature from a clean machine;
4. review `git diff <last-pushed>..HEAD` and the complete commit list;
5. confirm `git status --porcelain` is empty;
6. confirm all release artifacts name the same HEAD;
7. create the final release commit only if tracked release metadata changed;
8. create an owner-approved signed or annotated `v1.0.0` tag;
9. push `main` and the tag to `origin` only now.

If any final check fails, do not push. Fix in a new commit and repeat every
revision-bound qualification step affected by the new HEAD.

## 6. Baseline verification commands

Run these immediately when GLM-5.3 takes over and after every code batch:

```powershell
git status --short
git rev-parse HEAD
$python = "C:\Users\cy\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$env:PYTHONPATH = "$(Resolve-Path .\.tooling);$(Resolve-Path .)"
.\.tooling\bin\ruff.exe check .
& $python -m mypy uga apps
& $python -m pytest -q
npm ci
npm run typecheck
npm run build
Push-Location native
cargo fmt --all --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cargo build --workspace --release --locked
Pop-Location
git diff --check
```

For direct native capture tests, bind the exact built DLL before loading it:

```powershell
$dll = (Resolve-Path .\native\target\release\uga_capture.dll).Path
$env:UGA_NATIVE_CAPTURE_DLL = $dll
$env:UGA_NATIVE_CAPTURE_SHA256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $dll).Hash.ToLowerInvariant()
```

Also test on an exact Python 3.11 patch environment after the dependency lock is
created; the bundled workspace Python is 3.12.14 and is not a substitute for the
CI compatibility target.

## 7. Expected commit sequence

A reasonable remaining sequence is:

1. `build(ci): pin release toolchain inputs`;
2. one or more `fix(security): ...` commits from the final rescan;
3. Windows/qualification fixes discovered by the formal matrix;
4. `chore(license): select <owner-approved SPDX license>`;
5. model or adapter commits required for genuine VLM/real-game qualification;
6. `docs(qualification): record v1 evidence procedure` if tracked docs need a
   final correction;
7. `release: prepare UGA v1.0.0` only after all gates pass.

Generated evidence is normally ignored and does not itself require a commit.
Never squash unrelated batches merely to shorten history; the user explicitly
wants a commit after each completed modification batch.

## 8. Stop and ask the user when

Stop rather than guessing if any of these are unresolved:

- exact repository license choice;
- whether Fixture profiles are acceptable as the V1 “four-game” claim or real
  games are required;
- access to adequate GPU/remote training infrastructure for the production VLM;
- credentials, signing key, protected branch, or GitHub release permissions;
- any test would send physical input without a visible supervised target;
- any requested shortcut would make a gate or `releasable: true` claim untrue.

The correct handoff state is a clean repository with a truthful incomplete
qualification, not an overstated release.

## 9. Suggested first instruction for GLM-5.3

Use the following as the continuation prompt:

> Read `GLM53_HANDOFF_PLAN.md` and the complete UGA V1.1 source design before
> acting. Verify the repository is clean and based on the documented handoff
> commits. Continue from Batch A, make no false qualification claims, commit
> every completed modification batch, do not push intermediate work, and keep
> working through the ordered gates until UGA-075 is genuinely releasable or a
> documented owner decision is required.
