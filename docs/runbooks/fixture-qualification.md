# Fixture World qualification runbook

The UGA Fixture World is a developer-owned, offline visual target for supervised
capture, coordinate, input, mode-switch, Recorder, and Replay qualification. It
does not replace the required multi-game generalization matrix.

## Prepare

```powershell
python -m pip install -e .
uga-example-game --headless-smoke
uga-example-game
```

The second command must report `success: true`. The third opens the fixture and
must be launched by the operator because subsequent focus and physical-input
checks can affect the desktop.

Fixture controls are WASD movement, mouse heading, E interaction, Escape for the
GUI menu, and R to reset. The target profile is
`configs/games/uga-fixture-world.yaml` and requires the exact title
`UGA Fixture World`.

Keep the fixture window completely visible for the whole run. A window that
overlaps the fixture swallows the scripted resume click (the world stays in its
GUI state and never succeeds), degrades capture of the occluded window, and
steals focus that the harness must fight to restore; the qualification aborts
loudly when it detects the click point is covered. Park the fixture away from
consoles — a second monitor works well — and do not interact with the machine
while the run is in progress.

Four deterministic scenarios are available for dataset and generalization work:
`exploration`, `realtime_control`, `gui_navigation`, and the locked-test candidate
`heldout_diagonal`. Launch and qualify a non-default scenario with matching
`--scenario` arguments (the game window and the qualification must agree or the
scripted movement goes the wrong way), for example:

```powershell
uga-example-game --scenario realtime_control
uga-qualify fixture --scenario realtime_control --duration-seconds 600 `
  --episode-root runs/qualification-v1/episodes `
  --output runs/qualification-v1/realtime-control.json `
  --allow-physical-input
```

## Capture checks

With exactly one fixture window open:

```powershell
uga-capture-probe --title '^UGA Fixture World$' --frames 120 `
  --output runs/qualification-v1/capture/fixture-smoke.json

uga-capture-probe --title '^UGA Fixture World$' --duration-seconds 1800 `
  --target-fps 60 `
  --output runs/qualification-v1/capture/fixture-soak.json
```

During the soak, the operator exercises resize, minimize/restore, alt-tab, DPI,
and monitor transitions according to the release checklist. A report is useful
evidence only when frame count, effective FPS, timestamp regressions, resolution
changes, backend transitions, and operator notes are reviewed together.

The probe accumulates diagnostics without retaining frame pixel buffers, so a
long soak has bounded frame-memory use; only latency samples are retained.

## Integrated physical-input and Recorder check

With the fixture visible, run the explicit opt-in qualification command. It
uses the exact target title, developer-owned safety policy, focus and integrity
guards, leases, arbitration, the 30 Hz scheduler, physical SendInput, MP4 and
Parquet recording, canonical training targets, replay verification, and dataset
validation in one run. Long runs acquire a fresh short lease for each five-second
control cycle so a transient focus loss cannot revive stale queued input.
Fixture motor features contain normalized pixel-space player/target geometry and
the selected Game Profile scenario identity; the latter prevents distinct known
control profiles from collapsing when a previous cycle ends with overlapping
sprites.
Native capture loss is handled within the same Episode by retiring the failed
backend, selecting the next ranked backend, and recording the transition and
error in both the timeline and qualification report.

```powershell
uga-qualify fixture `
  --duration-seconds 8 `
  --backend gdi_fallback `
  --episode-root runs/qualification-v1/episodes `
  --output runs/qualification-v1/fixture-smoke.json `
  --allow-physical-input `
  --exercise-focus-loss `
  --exercise-emergency-hotkey
```

The command intentionally refuses to run without `--allow-physical-input`.
Ctrl+Shift+F12 remains the global emergency stop. Use a 600-second duration for
the Recorder acceptance run after the smoke report passes.

## Dataset and benchmark checks

```powershell
uga-dataset validate <episode> --output <quality-report.json>
uga-dataset process <episode> --output <aligned-samples.json>
uga-dataset view <episode> --output <dataset-viewer.html>
uga-dataset manifest <dataset-manifest.json> --root <dataset-root>

uga-train prepare-motor-samples `
  --episode <accepted-episode> `
  --output <motor-samples.jsonl>

# Release-volume owned corpus: 100 minutes for each Train A/B/C scenario,
# plus 10 minutes for the locked held-out scenario. This takes 5h10m wall time.
uga-qualify corpus `
  --output-root runs/qualification-v1/corpus `
  --allow-physical-input

uga-train motor `
  --samples runs/qualification-v1/corpus/motor-samples.jsonl `
  --dataset-manifest runs/qualification-v1/corpus/dataset-manifest.json `
  --dataset-root runs/qualification-v1/corpus/episodes `
  --config configs/training/fixture_motor_bc.yaml `
  --output runs/qualification-v1/models/fixture-motor-v1 `
  --policy-version fixture-motor-v1 `
  --source-revision <git-revision> `
  --base-model-license project-owner-controlled

uga-benchmark fixture `
  --config configs/benchmarks/uga-bench-fixture.yaml `
  --artifact runs/qualification-v1/models/fixture-motor-v1/training-artifact.json `
  --output runs/qualification-v1/models/fixture-benchmark-runs.jsonl `
  --report runs/qualification-v1/models/fixture-benchmark-report.json

uga-benchmark validate-config configs/benchmarks/uga-bench-smoke.yaml
uga-benchmark summarize <benchmark-runs.jsonl> `
  --config <benchmark-config.yaml> `
  --output <benchmark-report.json>
```

Do not record a gate as passed until its complete artifact set is inside the
qualification evidence root and has been independently reviewed.
