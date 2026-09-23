# Safe target-profile authoring

Read this reference when creating or modifying a YAML target profile.

Start from `configs/games/generic-visual-game.example.yaml`; copy it to a new file and replace every `CHANGE_ME`.

## Required decisions

- `game.id`: stable lowercase identifier with hyphens.
- `process.executable`: exact executable name or a narrow allowed list.
- `window.title_pattern`: anchored regular expression that matches only the target window.
- `camera.type`: `absolute_pointer` for GUI-heavy targets; `relative_mouse` for camera-look controls.
- `controls`: only bindings that have been manually verified.
- `no_click_regions`: title bars, overlays, account controls, or non-game chrome.
- `critical_action_terms`: authentication, payment, purchase, identity, deletion, installation, transfer, or other irreversible actions relevant to the target.

## Calibration rule

Do not invent normalized hotspots. A hotspot is allowed only after the operator identifies it on a real screenshot or verifies it in a supervised run. Keep optional return/close hotspots absent until then.

## Strategy rule

Use `model-first` for a new game. Add deterministic OCR rules only for repeatedly observed states with:

- at least two independent page anchors;
- an unambiguous control;
- an explicit expected visual effect;
- a cooldown or terminal state;
- positive and negative tests using real or representative OCR regions.

Never let a generic word such as “claim”, “continue”, or “confirm” authorize clicks across unrelated pages. Reward and account actions require workflow-specific context.

## Validation

The generic launcher rejects remaining `CHANGE_ME` placeholders. Also load the profile through the application or run a bounded launch; configuration parsing alone does not prove that the title pattern or coordinates are correct on the physical machine.
