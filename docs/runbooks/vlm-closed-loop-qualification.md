# VLM closed-loop qualification runbook

This runbook qualifies the grounded GUI path separately from the Fast Brain
`ActionChunk` path. A successful one-goal run is a development smoke, not the
100-Episode MuMu acceptance and not UGA-075 release approval.

## Prerequisites

1. Install the base and Windows CPython 3.12 vision locks:

   ```powershell
   python -m pip install --require-hashes -r requirements-lock.txt
   python -m pip install --require-hashes -r requirements-vision-lock.txt
   ```

2. Build `native/target/release/uga_capture.dll` and set
   `UGA_NATIVE_CAPTURE_DLL` plus its lower-case SHA-256 in
   `UGA_NATIVE_CAPTURE_SHA256`.
3. Start LM Studio's OpenAI-compatible local server and load the configured
   Qwen3-VL-4B model. Verify `http://127.0.0.1:1234/v1/models` before enabling
   input.
4. Start the owned MuMu instance, keep exactly one window matching
   `^MuMu安卓设备-\d+$` visible, and use `configs/games/mumu-xianyu.yaml`.
5. Do not operate the mouse or keyboard during the supervised run. Keep
   `Ctrl+Shift+F12` available as the emergency stop.

## Single-goal development smoke

Reset MuMu to its launcher, then run:

```powershell
python -m apps.agent run `
  --profile configs/games/mumu-xianyu.yaml `
  --policy vlm `
  --goal '打开安卓系统设置，看到设置页面后停止' `
  --vlm-base-url http://127.0.0.1:1234/v1 `
  --vlm-model qwen3-vl-4b-instruct `
  --vlm-no-thinking `
  --vlm-decision-interval 1 `
  --vlm-timeout-seconds 60 `
  --vision-mode local `
  --ocr auto `
  --max-recoveries 2 `
  --duration-seconds 60 `
  --observation-hz 5 `
  --record runs/qualification-vlm/episodes `
  --qualification-project-root .
```

Accept the smoke only when `run.json` reports `success` and
`goal_confirmed`, the planner journal contains two fresh `DONE` observations,
and every scheduled physical event executed. A logical click normally records
three physical scheduler events: pointer move, button down, and button up.
`WAIT`, `DONE`, and `ABSTAIN` must schedule none.

`--qualification-project-root` refuses a dirty checkout and writes the full Git
HEAD, clean-tree flag, and model id into both Episode metadata and `run.json`.
Omit it for ordinary development runs; Episodes without this binding are never
accepted by the hard live report.

For capture decoupling, require `capture_gap_p95_ns <= 350000000` and
`capture_gap_max_ns <= 500000000` while the local model is in flight. Inspect
every `reobserve` entry: stale generations and changed target pixels must never
have a corresponding scheduled action.

## Hard qualification layers

The following evidence remains mandatory after a smoke passes:

- Offline corpus: at least 200 annotated samples in the agreed 80/40/40/40
  categories. Require 100% schema validity, text F1/action kind/bbox hit at
  least 0.95, median center error at most 0.025, p95 at most 0.05,
  `WAIT`/`DONE` physical-error rate at most 0.01, and zero forbidden actions.
- Fixture + MuMu: 20 deterministic low-risk goals repeated five times. Require
  at least 95 successful Episodes, zero wrong-window/wrong-target/critical
  errors, zero stale action execution, action execution ratio at least 0.99,
  all injected loops detected within two repeated rounds, and recovery
  exhaustion always stopping by the configured maximum of two.
- Soak: a continuous 30-minute run with no uncontrolled input, infinite loop,
  or Recorder discontinuity.
- Authorized-game generalization: ten low-risk goals repeated three times,
  recorded and manually reviewed. This is a separate report and cannot replace
  Fixture + MuMu qualification.

Store raw Episodes outside Git under `runs/`. Hash the Episode `run.json`,
`metrics.json`, `planner.jsonl`, video, and checksum manifest. The current
release ledger accepts only its typed source-bound artifacts; a prose smoke
report or copied hash is diagnostic evidence and cannot promote UGA-075.

Create a `uga.vlm_live_plan` v1.1 JSON document beside an `episodes/` directory.
It binds the same `source_revision` and lists each Episode's `episode_id`,
`goal_id`, zero-based repetition, `task` or injected-`loop` role, manual review
status, wrong-window/target/critical-error findings, and loop detection rounds.
Then aggregate the matrix:

```powershell
uga-qualify vlm-live-report `
  --plan runs/qualification-vlm/live/plan.json `
  --episodes runs/qualification-vlm/live/episodes `
  --output runs/qualification-vlm/live/report.json `
  --project-root .
```

The verifier re-hashes every required Episode file, runs deterministic Replay
validation, checks the terminal planner diagnostics and action-effect counters,
and rejects incomplete cohorts, stale source bindings, pending/unaccounted
actions, non-action physical input, more than two repeated ineffective actions,
more than two recoveries, or capture gaps above the limits.

### Offline report

Each annotation JSONL row must include `sample_id`, one of the four categories,
the goal, one to three hash-verified relative frame references, `ocr_truth`,
`expected_kind`, `expected_action_kind`, normalized `expected_bbox`, expected
effect, goal status, and forbidden decision/action kinds and regions. Each
prediction row uses the same sample id and records the full clean source
revision, model id, Schema validity, decision/action kind, visible text,
normalized target bbox, and wrong-window flag.

Place the annotations, predictions, and referenced frames below one evidence
directory. Generate predictions from a clean committed checkout; the command is
append-only and resumes after the last durable sample if interrupted:

```powershell
uga-benchmark grounding-run `
  --annotations runs/qualification-vlm/offline/annotations.jsonl `
  --output runs/qualification-vlm/offline/predictions.jsonl `
  --base-url http://127.0.0.1:1234/v1 `
  --model qwen3-vl-4b-instruct `
  --no-thinking `
  --project-root .
```

RapidOCR is mandatory for this hard run. Every prediction records the exact
source revision, model id, Schema result, OCR text, grounded action and bbox,
latency, and a digest of the raw model reply. Infrastructure failures stop the
run without discarding completed rows. After the cohort completes, build the
report:

```powershell
uga-benchmark grounding-report `
  --annotations runs/qualification-vlm/offline/annotations.jsonl `
  --predictions runs/qualification-vlm/offline/predictions.jsonl `
  --output runs/qualification-vlm/offline/report.json `
  --project-root .
```

The command recomputes all metrics, enforces the 80/40/40/40 minimum cohort,
hashes the inputs and referenced frame set, binds the report to Git HEAD, and
fails if any threshold is missed. A malformed model reply must be recorded as
`schema_valid: false`; it is counted as a failure and never repaired by the
evaluator.
