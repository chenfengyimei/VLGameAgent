# PR3 integration with the merged PR2 runtime hardening

## Cause and history

PR3 was based on `c4af90632865b4d8e758d6eabc84bd410ada38aa`.
PR2 subsequently advanced main to `17856c7210d493ef6e7b670397596dcc70cca65d`
on 2026-09-17. Both branches implemented overlapping runtime contracts.
The resulting 24 conflicted paths require a semantic merge, not blanket
`ours`/`theirs` selection. This merge retains both histories and updates PR3;
it does not merge PR3 into main or rewrite either branch history.

## Reconciled contracts

- Keep main's shared console/direct parser, early-stop latch, bounded cleanup,
  fatal recording classification and one-shot encoder close. Preserve PR3's
  separately bounded perception and asynchronous diagnostic journal shutdown.
- Keep PR3's in-flight-aware recorder channel, capture-source retirement and
  fallback operation. Retain main's `capture_operation_timeout_s`,
  `recording_max_frames`, `recording_max_bytes`, and
  `recording_close_timeout_s` keyword names as compatibility aliases.
  If every source retires, the run fails fatally instead of waiting forever.
  A writer failure is detected even when no further frame is produced.
- Retain external run cancellation and immediate-completion stale-result
  auditing while sharing the decision budget across inference, repair,
  perception and verification. Timeout/cancellation revokes input authority;
  a blocked worker cannot be reused to accumulate detached threads.
  The main `Deadline` context also bounds nested PR3 request budgets.
- Combine sensitive page/action cues, including split OCR credential phrases.
  Sensitive observations reset pending goal/effect evidence before resume.
  Retain main's handoff diagnostic name and PR3's owner-required reason.
  PR3's final visual, generation and execution guards remain in place.
- Keep main's indexed replay lookups, primitive/proposal receipt validation,
  one-shot finalization and source Episode immutability; add PR3's duplicate
  observation and logical-parent consistency checks and actual video PTS.
- Keep Python native handle serialization and PR3's stop-before-return latch,
  validation before pointer copies, main's bounded error strings and native
  destruction errors. Keep Rust's abandoned-worker cap and bounded teardown;
  use the same 0..2000 ms per-capture range on both ABI sides.
- Keep prefixed GLM-family capability/effort validation together with PR3's
  protected request fields, output-budget floor and image limit. No model API
  was called and no live provider qualification is claimed.
- Keep fresh per-group fixture captures and distinct movement, relative-look
  and interaction labels. Reset/menu/resume diagnostics do not become motor
  labels. A qualification run must execute all scheduled fixture primitives.

## Stable export and duration APIs

`uga-dataset gui-export EPISODE --output NEW_DIRECTORY` retains main's
receipt-qualified PNG images, normalized coordinates and explicit lossy-video
geometry. The destination must be outside the source and must not exist.

`uga-dataset gui-export-references EPISODE... --output samples.jsonl` adds
PR3's atomic reference-only JSONL output. It does not claim to extract image
tensors and cannot write inside any source Episode. Both exporters accept
qualified legacy semantic-layer `GuiAction` rows as well as explicit GUI rows.

`qualified_duration_ns` retains main's union of pre-capture-to-last-receipt
evidence spans. `active_execution_duration_ns` separately reports the union
of first-to-last executed primitive spans. Neither uses an action's TTL.
An instantaneous action can have zero active duration and positive observed
evidence coverage. Existing manifest hour calculations retain main's evidence
coverage meaning; they must not be described as continuous physical activity.

The shared public CLI retains the main decision timeout default of 90 seconds
and adds a 10-second perception timeout. An explicitly supplied timeout is
passed through both public entry points identically. Older PR3 documentation
showing `gui-export ... --output samples.jsonl` or a 60-second CLI default
is historical and is superseded by this integration note.

## Validation and review

Both branches' test suites are retained. Colliding files have distinct names:
`test_gui_training_export.py` exercises actual PNG extraction and
`test_gui_reference_export.py` exercises reference JSONL;
`test_native_lifecycle_bounds.py` and `test_native_stop_latch.py` cover their
respective native contracts. The original fixture test now verifies three
independent logical intents instead of discarding main's look/interact labels.
The mixed-table replay fixture constructs its tables before lookup indexes,
rather than mutating the read-only replay after indexing.

`test_merge_runtime_contracts.py` adds nine combined-contract regressions.
Local portable tests, Ruff and strict win32-targeted mypy are supplemented
by an exact-tree Windows build, native DLL tests and PR checks. Results and
commit/tree identities are recorded in the PR acceptance comment, not inferred
from either parent's earlier CI status.

No checks were removed or disabled. Paid model calls, real-game input, multi-
monitor/UIPI qualification, long-duration reliability and five-stage GPU
training remain separate release gates. This merge does not promote V1.
