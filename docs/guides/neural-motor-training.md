# Neural motor feature-head development

This is a real gradient-trained PyTorch MLP for recorded features and canonical
motor actions. It retains the deterministic linear baseline. It is NOT a visual
foundation model, all five training stages, learned temporal planning or V1
qualification. Nothing here downloads a backbone, calls a paid provider, records
user demonstrations or enables physical keyboard/mouse input.

## Isolated CPU environment

Use a clean checkout and CPython 3.11.9 in a separate virtual environment:

```sh
python -m pip install --require-hashes -r requirements-lock.txt
# Linux x86-64 (use exactly one platform lock):
python -m pip install --require-hashes -r requirements-neural-cpu-linux.txt
# Windows x86-64 alternative:
# python -m pip install --require-hashes -r requirements-neural-cpu-win32.txt
python -m pip install --no-deps --no-build-isolation -e .
python -c "import torch; assert torch.__version__ == '2.10.0+cpu'"
```

The optional `neural` extra declares dependencies for custom environments, but
is not a substitute for these tested hash locks. Normal installation, CLI help,
portable checkpoint verification and neural inference do not import PyTorch.
CUDA is an explicit config choice and fails when unavailable; no CUDA lock or
GPU performance evidence is provided. There is no automatic CPU fallback.

## Source-backed data

Use receipt-qualified Episodes with recorded `features` and canonical motor
labels. Review a DatasetManifest with real checksum/content hashes, license
records, receipt qualification and source revision. Existing dataset split
rules separate Episode, session, player, game and identical content. Do not
rename identities to circumvent those rules.

Encoder version and license are operator attestations. Feature-value matching
against a recording does not prove which visual network generated the features.
The motor feature head does not train on the separate PNG `gui-export` or
reference-only `gui-export-references` formats.

Export each split separately (replace example paths with reviewed Episodes):

```sh
uga-train prepare-motor-samples --episode data/train-episode --output runs/train.jsonl
uga-train prepare-motor-samples --episode data/validation-episode --output runs/validation.jsonl
```

Repeating `--episode` includes more Episodes in that split. Exports cannot write
inside source Episodes; duplicate sources are rejected. Training verifies exact
features, canonical axes/buttons, action and observation IDs against actual
execution-receipt-qualified source records. Duplicate samples, incomplete IDs,
boolean/string numeric coercion, nonfinite values and wrong splits fail closed.
TRAIN and VALIDATION must be nonempty. Training checks manifest-wide split
metadata, but opens only selected train/validation Episode contents; held-out
TEST content remains unopened.

## Train

Review and commit `configs/training/neural_motor.json`. The public CLI binds
`--project-root` to the actual loaded source checkout and requires clean Git
HEAD; it cannot claim the SHA of an unrelated repository. Dataset recording
revision is stored separately and need not equal the trainer revision. Low-level
Python API callers remain responsible for accurate revision attestations.

```sh
uga-train neural-motor \
  --train-samples runs/train.jsonl \
  --validation-samples runs/validation.jsonl \
  --dataset-manifest data/dataset-manifest.json \
  --dataset-root data \
  --config configs/training/neural_motor.json \
  --output runs/neural-motor-v1 \
  --policy-version motor-dev/v1 \
  --encoder-version YOUR_RECORDED_ENCODER_VERSION \
  --encoder-license YOUR_REVIEWED_ENCODER_LICENSE \
  --project-root .
```

This multiline example uses shell backslashes. PowerShell requires a single
line or backtick continuations. Output must be a NEW directory outside all
source Episodes and input files. The pipeline snapshots the exact input bytes,
verifies original sources before and after fitting, and publishes the validated
`artifact.json` completion marker LAST. Failure removes only its own newly
created staging/output directory, never an existing output or source corpus.

Normalization and feature support are fitted on TRAIN only. The network is a
tanh hidden layer followed by four continuous outputs and nine button logits.
Movement uses MSE, camera axes use smooth-L1 (beta 0.1), and buttons use
BCE-with-logits. AdamW, clipping, explicit float32 allocation and a private batch
RNG are used. Model selection and early stopping use validation loss. Epoch zero
may remain best if fitting gives no improvement. Reports include actual framework,
device, seed, completed/best epochs and initial/final metrics. No TEST score is
used for training or selection. The caller's CPU RNG and ambient default dtype
are not changed.

Limits: 2,000,000 feature values, 250,000 parameters, 500,000,000 estimated
training-work units, 50,000,000 portable-evaluation-work units. This is a small
bounded feature head, not scalable foundation-model training. Reduce the selected
subset, hidden size or epochs if a budget is exceeded, and report that subset
honestly. `max_seconds` is a cooperative numerical-training budget checked around
batches/validation, not a hard kill of hung CUDA kernels or a source-I/O deadline.
Optimizer/checkpoint resume and distributed training are not implemented.

## Verify and held-out evaluation

Outputs: numeric `model.json`, train and validation JSONL snapshots, config,
dataset-manifest snapshot, metrics, and a fixed-file SHA-256 `artifact.json`.
No pickle, `torch.load`, dynamically executable model or remote code is used.

```sh
uga-train neural-verify runs/neural-motor-v1/artifact.json
uga-train neural-verify runs/neural-motor-v1/artifact.json --expected-sha256 TRUSTED_SHA256
uga-train prepare-motor-samples --episode data/test-episode --output runs/test.jsonl
uga-train neural-evaluate runs/neural-motor-v1/artifact.json \
  --samples runs/test.jsonl --dataset-root data --split test \
  --output runs/neural-motor-v1-test.json
```

Verification checks exact bytes, shapes, split identities, train-only statistics,
confidence evidence and recomputed portable metrics. Continuous metric rounding
has a small cross-platform tolerance; discrete decisions/counts are exact.
Without an independently trusted manifest digest, hashes prove internal
consistency, NOT authenticity: a complete self-consistent bundle can be replaced.
Portable verification does not reread original Episode contents; training and
explicit source-backed evaluation do. Evaluation snapshots samples and never
refits; its output cannot alter the artifact or sources. Do not repeatedly tune
against test results; that destroys held-out evaluation independence.

Loss evaluates raw network predictions; action accuracy and abstention metrics
also apply the support gate. These are different acceptance rules, not conflicting
statistics. Synthetic test success and offline metrics do not establish gameplay
success, physical-input safety or real held-out game generalization.

## Development-only policy loading

`load_neural_fast_policy` in `uga.training.neural_pipeline` requires an existing
VisualBackbone, an independently trusted artifact SHA-256, and explicit
`allow_development_model=True`. The encoder version must match. Inference uses
the exact verified model snapshot; a later file replacement cannot change it.

The decoder implements the existing ActionDecoder protocol. It cannot write to
an input backend. Existing leases, arbitration, deadlines, watchdog and operator
enablement remain mandatory. It is not automatically selected by the live VLM
runner. Default horizon is one tick; longer horizons are repeated predictions,
not a learned sequence policy.

Out-of-support features (per-dimension training bounds plus 5% span tolerance)
produce zero axes/buttons/confidence. Bounding boxes cannot detect all novel
combinations. Confidence is a validation action-accuracy Wilson lower bound times
the minimum button margin. It is a conservative aggregate heuristic, NOT a
calibrated per-frame safety probability. Low confidence uses the existing
StructuredFastPolicy reasoning gate and never implicitly resumes input.

## Validation and remaining release gates

Run `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 pytest tests/neural -q` (PowerShell: set
those environment variables first). Dedicated Linux/Windows CPU CI explicitly
imports the pinned backend before pytest, so a missing backend fails instead of
silently skipping the neural suite. Runtime-only CI can skip optional neural
tests; verify CPU matrix outcomes independently.

Coverage includes nonlinear fitting with real gradients and learned buttons,
portable inference parity, validation isolation, RNG/default dtype, unavailable
CUDA, cancellation/resource budgets, causal source checks, immutable output,
source identity, tampered metrics/confidence/statistics, relocation, runtime
hash pinning and full public CLI train/verify/evaluate round trips. Synthetic
owned fixture receipts are not actual OS input evidence.

Remaining: a licensed visual backbone integration, instruction/recovery/reasoning
and DAgger neural stages, temporal policy, GPU/distributed qualification, real
held-out gameplay and long-duration reliability. Every new artifact/report sets
`release_qualified=false`. These artifacts have a separate schema and do not
satisfy legacy linear `verify` or formal V1 `stage-report` requirements.

Rollback a stopped runtime to a coherent earlier version. Original Episodes
remain immutable; keep derived neural artifacts separate. Existing linear
checkpoints, CLI commands and safety rules remain supported.
