# VLM closed-loop MuMu live smoke

Date: 2026-09-13  
Closed-loop candidate: `a9b2fb17be6cfe1267a1ada6631eb7ee395193a4`  
Target: owned MuMu Android 15 instance, window `MuMu安卓设备-1`  
Planner: local LM Studio `qwen3-vl-4b-instruct`  
Goal: open Android system settings and stop after the settings page is visible

## Result

The final supervised run succeeded. The model grounded the visible gear icon,
the runtime derived the click from its normalized bounding box, one logical
click was executed, and the loop stopped only after two fresh `DONE` decisions
reported the completed goal. A post-run GDI screenshot visibly shows Android's
Chinese Settings page. No stale proposal, `WAIT`, `DONE`, or `ABSTAIN` decision
produced physical input.

Final Episode: `mumu-xianyu-20260913-084745-6a5c8d54`

| Measurement | Observed | Smoke threshold |
|---|---:|---:|
| Result / termination | `success` / `goal_confirmed` | required |
| Goal confidence | 1.00 | at least 0.85 |
| Accepted capture frames | 372 | informational |
| WGC / GDI heartbeat frames | 276 / 96 | informational |
| Capture gap p95 | 284.134 ms | at most 350 ms |
| Capture gap maximum | 375.819 ms | at most 500 ms |
| Scheduled / executed physical events | 3 / 3 | ratio at least 0.99 |
| Logical GUI actions | 1 click | one action per observation |
| Stale actions executed | 0 | 0 |
| Recovery actions | 0 | at most 2 |

Planner dispositions were `execute`, `reobserve`, `reobserve`, `terminate`.
The first `reobserve` rejected a now-changed target region while a second
inference was returning. The last two decisions were `DONE`; only the second
fresh confirmation terminated the Episode.

## Defects exposed and corrected

Two preceding runs were retained because they are useful negative evidence:

1. `mumu-xianyu-20260913-083458-92e50a8a` failed closed with no physical
   action. WGC recovered during inference, so the correct gear proposal became
   generation-stale and was discarded. The next model reply used pixel bbox
   values and failed the normalized Schema. This exposed that a stale discard
   incorrectly consumed the one local retry and that the Schema needed numeric
   bounds plus actionable repair feedback.
2. `mumu-xianyu-20260913-083802-6ab10030` reached `goal_confirmed`, but capture
   p95/max gaps were 508.232/578.887 ms. The fixed-period fallback loop could
   wait nearly two periods after a primary stall. Fallback scheduling now wakes
   at the exact primary-stall deadline and remains capped at 4 Hz.

The final run verifies both corrections: the same goal succeeds and both
capture gap thresholds pass.

## Local artifact integrity

The raw evidence is intentionally ignored by Git and remains under
`runs/qualification-vlm/`. These hashes identify the local files used for this
review:

| Artifact | SHA-256 |
|---|---|
| final `run.json` | `a6c48ff254e59408d608905697c16b8c6fbb94112b14591a0bea4cf98f659f08` |
| final `metrics.json` | `4dc32b187f28b37ff5ceae14c78c66815b2a438b98de181a33eabd1841cc601d` |
| final `planner.jsonl` | `da33f836ae129969b46fc893ddcbd1296c72f6d7874a9cd3f2ac3f16cd74d68e` |
| post-run screenshot | `569233e3b672adb662142e8b2bbd54085532c841b3dcf2bef877dde1cc82be66` |

At the candidate revision, Ruff and strict mypy passed; pytest reported 364
passed, one opt-in physical-input test skipped, and 89 subtests passed. Cargo
format/clippy/tests, TypeScript typecheck/build, the hashed vision dependency
lock dry run, and isolated Python sdist/wheel builds also passed.

## Qualification boundary

This is a successful real-model, real-capture, real-input development smoke. It
does not satisfy the planned 200-sample offline corpus, 100-Episode Fixture +
MuMu matrix, 30-minute visual-loop soak, authorized-game report, five-hour
training corpus, five model stages, or the final UGA-075 source-bound ledger.
Those items remain open and must not be inferred from this result.

