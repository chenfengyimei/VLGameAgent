# GUI model loop delivery, 2026-09-18

Base: `97030c7d5fb1cc27b6464392511059317fc857d5` (merged PR4).
Scope: GUI perception/planning, provider input/output, grounding, bounded operations and
observed effect feedback. No neural training changes, live inference or user input testing.

## Modules

1. Bounded strict reply parser, explicit coordinate contracts and schema alignment.
2. Shared compact/full prompts and current/history/detail image map with real pixel budgets.
3. Spatially bounded OCR correction and independent operation-aware secondary verification.
4. Receipt-complete, fresh-frame explicit postconditions and bounded task feedback.
5. Safe supported pointer operations and target-local final visual comparison.
6. Model-first public composition, confirmed key inventory, separate verifier settings,
   Qwen/GLM request switches, bounded wire inputs and rejection of unauthorized tool responses.
7. Actual planner -> HTTP contract -> controller -> arbiter/scheduler -> DryRun receipts ->
   postcondition -> replanning integration across both provider names and coordinate scales.
8. Updated usage, migration and measurable live-validation boundaries.

The code retains the existing lease, deadline, foreground and sensitive-page protections.
Both modes still abstain on unproven output. No blanket catch-and-click fallback was added.
The detailed [GUI guide](../guides/gui-model-closed-loop.md) is the current contract.

Validation is bound to the final commit/tree in the PR acceptance record, not to earlier
intermediate counts. Unit/integration tests use fake provider responses and synthetic frames;
CI success does not establish live Qwen/GLM accuracy, end-user hardware reliability or V1.
