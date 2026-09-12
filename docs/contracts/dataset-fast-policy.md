# Dataset and Fast Policy contract

Status: implementation contract frozen for UGA-054 through UGA-070.

Recorded Episodes are the only source format accepted by the dataset pipeline.
Validation rejects incomplete manifests, broken checksums, timestamp regressions,
missing action/observation links, and invalid provenance before processing.
Dataset splits operate on episode, session, player, and game ownership groups so
frames from the same trajectory cannot leak across train and evaluation sets.
The persisted Dataset Manifest anchors every Episode checksum digest and records
ownership, category, quality result, duration, locked held-out games, source
revision, and explicit dataset-license terms.

The Fast Policy consumes pixel-derived observations and temporal context. It
returns a versioned `ActionChunk` of canonical actions with explicit creation,
effective, and expiration times. It cannot call an input backend; chunks still
pass through the lease, arbiter, scheduler, focus, and safety boundaries.

Qwen features and the temporal action decoder are injected behind contracts.
The deterministic behavior-cloning trainer and VeOmni launcher produce
self-describing artifact manifests. A checkpoint is not qualified merely because
it can be serialized: dataset license, offline metrics, latency, closed-loop
evaluation, and held-out-game results remain mandatory release evidence.

Motor-training JSONL records must carry `episode_id`, `observation_id`, and
`action_id`. The training entry point verifies that each Episode belongs to the
Manifest train split, has accepted quality, and that the referenced Observation
and Action exist and are linked in the recorded Parquet tables. Its artifact
manifest binds the checkpoint, Dataset Manifest, training configuration, and
sample file with SHA-256 digests; `uga-train verify` rechecks all four.

Dataset, sample, training, checkpoint, benchmark, and video readers share finite
byte, record, dimension, epoch, work, latency-sample, and decoded-frame budgets.
JSONL is read incrementally with per-line limits, Parquet metadata is checked
before table materialization, and video decoding stops as soon as the expected
or global frame ceiling is exceeded. Every overrun fails closed with a contract
violation (or a rejected Dataset validation report).

Offline evaluation includes movement MSE/accuracy, camera MAE/smoothness,
button accuracy/F1, whole-ActionChunk accuracy, mode accuracy, confidence error,
and reasoning-gate precision/recall/F1. Slow inference degrades through the
configured 5, 4, 2.5, and 2 Hz ladder while action horizons remain capped.

Instruction, recovery, reasoning-gate, and DAgger records preserve source
episode IDs and split ownership. DAgger collection is operator-supervised and
may append demonstrations, but may not record data whose license/provenance is
unknown.
Instruction, recovery, and DAgger retraining APIs reject records outside the
train split so held-out gameplay cannot silently enter a checkpoint.

Each production stage is promoted with `uga-train stage-report`. The command
requires a verified `uga.training_artifact`, a detected NVIDIA GPU, and typed
`uga.offline_metrics` and `uga.closed_loop_metrics` JSON reports. Both metric
reports bind the source revision, stage, Dataset Manifest digest, and training
artifact digest. They contain finite numeric `metrics` plus non-empty `checks`
entries (`metric`, `operator`, and `threshold`); the qualification command
recomputes every comparison instead of trusting the top-level `passed` value.
Offline reports also declare a positive `samples` count. Closed-loop reports
declare positive `episodes` and a non-empty distinct `games` list.

`uga-train qualification-report` accepts exactly the motor, instruction,
recovery, reasoning-gate, and DAgger stage reports. The resulting
`uga.model_qualification` recursively verifies and hash-binds every stage
report, artifact manifest, metric report, Dataset Manifest, training input, and
model file whenever the release ledger is checked. Merely copying plausible
SHA-256 strings into an aggregate JSON document is not qualification evidence.

The optional OpenCUA bridge targets the public AgentNetBench trajectory shape
without making OpenCUA a runtime dependency. Exported GUI steps contain the
high-level task description, image reference, normalized coordinates, and
ground-truth actions; unsafe image paths and action types that cannot be mapped
losslessly are rejected. See the official [OpenCUA repository](https://github.com/xlang-ai/OpenCUA)
and [AgentNetBench data/evaluation contract](https://github.com/xlang-ai/OpenCUA/blob/main/evaluation/agentnetbench/README.md).
