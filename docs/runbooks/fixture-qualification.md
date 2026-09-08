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

## Dataset and benchmark checks

```powershell
uga-dataset validate <episode> --output <quality-report.json>
uga-dataset process <episode> --output <aligned-samples.json>
uga-dataset view <episode> --output <dataset-viewer.html>
uga-dataset manifest <dataset-manifest.json> --root <dataset-root>

uga-benchmark validate-config configs/benchmarks/uga-bench-smoke.yaml
uga-benchmark summarize <benchmark-runs.jsonl> --output <benchmark-report.json>
```

Do not record a gate as passed until its complete artifact set is inside the
qualification evidence root and has been independently reviewed.
