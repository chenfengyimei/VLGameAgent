# Neural motor development delivery

Base: `f279eea822b3f04f96fbca5dc3f5d6234607c3da` (merged PR3).

## Module sequence

1. Strict motor values/JSON, split-aware provenance, source-immutable exports.
2. Bounded numeric MLP checkpoints, portable inference, ActionDecoder protocol.
3. Actual motor BC gradients, learned axes/buttons, train-only normalization,
   validation-only selection, explicit device and cooperative resource limits.
4. Causal source verification, snapshot/hash manifests, new-output publication,
   separate held-out evaluation and pinned opt-in runtime loading.
5. Public CLI commands, optional dependency hash locks and Linux/Windows CPU CI.
6. Actual source-checkout binding and independent normalization/confidence audit.
7. Operator guide, rollback/migration and explicit qualification boundaries.

Modules are separate commits. Exact final commit/tree identities and CI outcomes
belong in the PR acceptance record, not inferred from a previous code tree.

A workspace reset occurred before initial publication. The working copy was
restored from the pinned main bundle and the development records; negative
regressions and the final suite were rerun. Superseded local commit hashes and
pre-reset counts must not be presented as evidence for the final remote tip.

## Acceptance

- Base negative data tests reproduce unsafe coercions/source writes and pass
  after correction without deleting existing safety checks.
- Real optimizer steps improve a nonlinear synthetic problem and condition
  button predictions on observations. No GPU device or gameplay is fabricated.
- Fitting does not open Test Episode content. Wrong splits, changed recorded
  features and duplicate provenance cannot produce a completed artifact.
- Model/config/sample snapshots verify after relocation; changes after reading
  hash-checked source bytes cannot swap the runtime's returned model.
- Runtime imports stay torch-free; optional CPU CI requires the pinned backend.
- Existing Python, Rust, UI and native integration gates remain in place.

## Limits

This is motor feature-head development, not all five neural stages. No backbone
weights, real user corpus, paid API call, physical-input benchmark or GPU evidence
is provided. CPU tests use synthetic receipt-backed fixtures. CUDA remains
unmeasured; cooperative deadlines do not isolate a hung driver. Dataset/encoder
license assertions require human review. New schemas explicitly remain
release_qualified=false and do not promote formal V1.

See [the full guide](../guides/neural-motor-training.md) for exact commands,
trust boundaries, data provenance, migration and rollback.
