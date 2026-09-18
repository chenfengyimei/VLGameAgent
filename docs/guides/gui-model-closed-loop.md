# GUI-first closed loop: Qwen3-VL-4B and GLM-4.6V

This guide covers the live screenshot-to-GUI path, not neural motor training.
Both models use the same local, versioned action contract (`gui-grounding/2.0`).
The model proposes ONE operation, never an unchecked program or action list.

## Runtime path

1. Capture the selected window and build fresh OCR/context on the monotonic timeline.
2. Apply the existing sensitive-page, target-identity, cancellation and ownership guards.
3. Send a current overview, optional same-window history and labelled read-only detail crops.
4. Parse and locally validate one ACT / WAIT / DONE / ABSTAIN object. A rejected answer gets
   at most one format-repair request; schema fallback and repairs share the existing request
   and total decision budgets. Repeated failure abstains, never guesses a click.
5. Refine OCR coordinates only near the proposed instance. Ambiguous local duplicates
   abstain; a remote matching label is not a license to move the click across the screen.
6. Optionally obtain an independent second-model verdict, then recheck the latest frame.
7. Resolve coordinates, authorize through the existing lease/arbiter, and schedule bounded
   physical primitives. Each primitive still checks current runtime/window/task/geometry and
   frame age. The final pixel comparison covers the target, not irrelevant animated scenery.
8. Wait for ALL physical receipts, then observe the declared effect on fresh frames after
   the LAST executed primitive. No new planning happens while an effect is pending.
9. Feed bounded, task-scoped execution/effect facts into the next prompt. Only independent
   registered goal evidence can finish the task; an effective click is not task success.

No step enables input by itself or bypasses foreground, emergency-stop, integrity, recovery
budget or lease checks. Pay/credentials/agreement/destructive pages remain operator-owned.

## Models and provider options

Use the exact model ID exposed by the configured server. Qwen3-VL Instruct and Thinking are
separate checkpoint editions. A GLM-style `thinking` object is not automatically sent to
Qwen3-VL; `--vlm-no-thinking` with a named Qwen3-VL Thinking checkpoint fails with an Instruct
hint. Backend-specific chat-template settings require explicit, reviewed `--vlm-extra-body`.
For GLM-4.6V the existing no-thinking option sends `thinking.type=disabled`.

`--vlm-json-object` asks a provider for JSON object mode rather than strict JSON Schema.
Both modes receive the SAME prompt constraints and local validation. Unsupported format
responses allow one cached downgrade to plain completion; malformed output does NOT remove
local validation. Returned tool calls, reasoning-only content and truncated output are not
executable actions. This client deliberately does not use the providers' native function calls.

The application retains temperature 0 unless explicitly configured in extra-body. This is
an operational starting point, NOT a claim of the vendor's optimal benchmark settings.
Changing quantization, backend or sampling requires a repeatable evaluation on your screens.

## Suggested initial setup

The following are PowerShell SINGLE-LINE examples. Review the profile, model endpoint and
operator-owned game first. They invoke the real runner, so use a supervised test environment.
Replace the goal with your exact task and use `--goal-evidence` only for genuine resulting-page
text, not text already present on a navigation button. A model name alone does not download it.

Local Qwen3-VL-4B-Instruct, model-first and compact output:

```powershell
python -m apps.agent run --profile configs/games/mumu-xianyu.yaml --policy vlm --vlm-base-url http://127.0.0.1:1234/v1 --vlm-model qwen3-vl-4b-instruct --goal "Open the sound settings page" --goal-evidence "Sound settings" --gui-planning-mode model-first --gui-coordinate-space normalized_1000 --vlm-compact-output --vlm-image-width 960 --vlm-temporal-frames 1 --vlm-target-crops 1 --vlm-max-output-tokens 1024 --vlm-timeout-seconds 60 --decision-timeout-seconds 90 --duration-seconds 60 --dashboard-port 8787
```

Standalone GLM-4.6V uses the same loop. Set `UGA_VLM_API_KEY` securely outside source files
and command history, then change the endpoint/model and add the GLM switches:

```powershell
python -m apps.agent run --profile configs/games/mumu-xianyu.yaml --policy vlm --vlm-base-url https://open.bigmodel.cn/api/paas/v4 --vlm-model glm-4.6v --vlm-api-key-env UGA_VLM_API_KEY --vlm-no-thinking --vlm-json-object --goal "Open the sound settings page" --goal-evidence "Sound settings" --gui-planning-mode model-first --gui-coordinate-space normalized_1000 --vlm-compact-output --vlm-image-width 960 --vlm-temporal-frames 1 --vlm-target-crops 1 --vlm-max-output-tokens 1024 --vlm-timeout-seconds 60 --decision-timeout-seconds 90 --duration-seconds 60 --dashboard-port 8787
```

Those English example evidence strings must be replaced with the actual UI language/text.
Chinese goals, labels and observed evidence are preserved as Unicode; no translation is
applied to coordinates or to exact label matching.

Optional Qwen planner + GLM verifier: append to the LOCAL command below. This explicitly
sends current screenshots to the verifier endpoint and may incur provider charges.
`UGA_VERIFIER_API_KEY` is independent and does not silently inherit the planner credential.

```text
--vision-mode hybrid --verifier-base-url https://open.bigmodel.cn/api/paas/v4 --verifier-model glm-4.6v --verifier-api-key-env UGA_VERIFIER_API_KEY --verifier-no-thinking --verifier-json-object --gui-verification always
```

`ambiguous` (default) uses the configured verifier for intermediate-confidence proposals;
`always` verifies every ordinary ACT proposal, including high-confidence ones. Runtime
recovery directives remain bounded deterministic recovery, not ordinary model proposals.
Always mode without an enabled verifier fails before activating the game window. The
verifier rejects/approves the specific operation; it does not generate a replacement click.
It receives current pixels, coordinates, goal and bounded OCR, not the planner's persuasive
explanation/confidence. Its response is independently validated.

## Planning and input settings

- Public runner default: `--gui-planning-mode model-first`. Registered game OCR shortcuts and
  model-back-intent relocation are disabled. OCR remains evidence and local refinement.
  `rules-first` explicitly restores existing registered shortcuts. `--vlm-ocr-task-fallback`
  has effect only there; it cannot silently override a model's WAIT in model-first mode.
- Python constructor compatibility: `enable_rule_fast_paths=True` and calibrated-intent
  routing remain legacy defaults for embedded callers. The public runner passes explicit
  safe settings. Embedded users should opt into model-first settings too.
- `--gui-coordinate-space unit` remains the public default for compatibility. The examples
  and MuMu helper select `normalized_1000` explicitly. There is NO magnitude-based detection.
- `--vlm-image-width` is 320..1280; resizing preserves aspect ratio. Use 960 initially and
  1280 for small text when model budget permits. Higher resolution is not automatically
  higher task accuracy; compare latency and errors on representative screens.
- `--vlm-temporal-frames 1` avoids small-model historical confusion. Up to three overviews
  are supported; history must be same window/geometry, not future-dated, and recent.
- `--vlm-target-crops 1` supplies an original-resolution goal/OCR magnifier when available.
  Crop count is a maximum, not a promise; no relevant region means no fabricated crop.
  Retry increases actual overview pixels up to 1280; a capped retry is not called an upgrade.
- `--perception-timeout-seconds` is independently 10 seconds by default, and the public
  total decision timeout remains 90 seconds. Verifier/repair work shares the decision budget.
- The MuMu helper now exposes `-GuiPlanningMode`, `-GuiCoordinateSpace`, `-ImageWidth`,
  `-TemporalFrames`, `-TargetCrops`; default output budget is 1024 instead of 256, and its
  old forced OCR fallback is opt-in. It does NOT enable a cloud verifier automatically.

## Output contract and coordinates

Compact example (full mode additionally requests scene/evidence/explanation/risk fields):

```json
{
  "kind": "act",
  "confidence": 0.93,
  "action": {
    "kind": "click",
    "target_label": "Settings",
    "target_bbox": [200, 300, 400, 400],
    "confidence": 0.93,
    "key": null,
    "scroll_delta": null,
    "effect": {"kind": "text_appears", "text": "Sound settings"}
  },
  "wait_reason": null
}
```

For thousand-grid input the above center is `(0.3,0.35)` internally. Unit-space uses
`[0.2,0.3,0.4,0.4]` instead. Boxes reference the CURRENT overview, not crop pixels, desktop
pixels, an earlier screenshot or the last attached image. The `image_map` states each image's
role, frame, size and crop extent. OCR boxes use the same selected external scale. The runtime
converts to physical coordinates through the existing window transform; negative monitor
origins and resized client images are covered by the mocked round-trip test.

Supported model operations: click, double_click, right_click, long_click (700 ms), scroll,
key and hotkey. Click boxes covering more than 25% of the image are rejected as panel-sized.
Scroll requires `scroll_delta` of -120/-240/-360/-480 (DOWN) or the positive equivalents (UP)
and a visible scrollable area. Every non-scroll operation uses null. Keys must exactly match
`confirmed_key_bindings`; an empty inventory means no keyboard operations. No arbitrary
chord parsing, credential typing, multi-step action lists or drag generation is enabled.

WAIT is `{"kind":"wait","confidence":0.9,"action":null,"wait_reason":"loading"}`.
Other reasons are animation/no_safe_action. ABSTAIN and DONE also have null action and null
wait_reason. Compact and full prompts have identical behavioral constraints. Legacy valid
replies without effect/scroll_delta remain readable; they cannot express scroll without a
delta and use conservative legacy effect checks. Confidence is not a calibrated probability
or an authorization. Duplicate fields, unknown fields, boolean/string numerics, out-of-range
coordinates, excessive text and unsupported operations are rejected locally.

## Execution and observable effects

Choose an effect, or null when a safe concrete condition cannot be specified:

- `text_appears`: literal text absent before the action, present after it.
- `text_disappears`: text present before, absent afterward with nonempty OCR evidence.
- `target_changes`: persistent target-region or known target control-state change.
- `scene_changes`: changed mode or substantial replaced OCR content, not one notification.

Non-text kinds require `text:null`. Explicit effects require two distinct post-action frames
separated by at least 100 ms and respect the existing absolute effect deadline. An old frame,
one transient notification, empty OCR or a model prediction cannot prove the effect.
The clock starts after the final executed primitive, not after queue acceptance or mouse-down.
Failed/partial/rejected actions produce feedback and bounded recovery, not positive evidence.
Long-click release may be neutralized early when its target changes during the hold; input
safety takes precedence over keeping a potentially stale hold active.

## Diagnose and evaluate

The dashboard decision detail records prompt version, coordinate space and image-map
metadata, as well as existing source/confidence/schema/receipt/effect facts. It does not dump
full prompts or image bytes. Reply previews and enabled local recordings still need privacy
review. No-data/uncertain states must not be interpreted as recognition success.

Run the new program-level tests without a model or physical input:

```text
python -m pytest tests/unit/test_gui_protocol.py tests/unit/test_gui_prompt_inputs.py tests/unit/test_gui_grounding_accuracy.py tests/unit/test_gui_effect_feedback.py tests/unit/test_gui_pointer_operations.py tests/unit/test_gui_model_configuration.py tests/integration/test_gui_model_roundtrip.py -q
```

Before live deployment, record a reviewed corpus of real game states: loading, dialogue,
small icons, disabled buttons, repeated labels, overlays, scrolling, sensitive pages and goal
completion. Compare both models with fixed model/quantization/server/prompt/settings versions.
Measure valid-JSON rate, correct target/operation rate, stale rejection rate, unnecessary
abstention, postcondition false positives, latency and complete-task success. Do not equate
mock pass counts with live recognition accuracy or increase confidence to bypass uncertainty.
No live provider calls or physical gameplay were used to qualify this change.

## References and migration

Primary model references checked 2026-09-18:
- https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct
- https://docs.z.ai/guides/vlm/glm-4.6v

These explain model families/interfaces; the bounded GUI schema, coordinate scale and
verification policy here are application contracts, not claims of provider-certified accuracy.
Source Episodes remain immutable. This change neither migrates neural model artifacts nor
promotes V1 qualification. Roll back the coherent code version only after stopping input.

## Post-merge reliability follow-up

The [GUI reliability review](../reviews/gui-reliability-2026-09-18.md) documents
final target-state revalidation, local pixel evidence for explicit effects,
acquisition-time settling and bounded baseline retention. No new launch flags
are required; uncertain or contradictory evidence causes re-observation rather
than an unconditional click or a false effect-success claim.
