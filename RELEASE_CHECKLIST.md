# UGA V1 release qualification

Packaging must not be interpreted as release approval. V1 is releasable only
when every gate below has recorded evidence.

## Development package evidence

- [x] Ruff, strict mypy, pytest, and Rust release compilation pass.
- [x] Wheel and source distribution build in isolated PEP 517 environments.
- [x] Wheel installs into a clean virtual environment without dependency
  resolution, and the installed agent/dashboard console entries run.
- [x] Bundled launcher resolves the adjacent native capture DLL and runs the
  installed lifecycle smoke entry.
- [x] Fixture World headless goal reaches `COMPLETE`; installed capture,
  dataset, benchmark, and qualification commands are present.
- [x] Build emits a machine-readable inventory of exact Python and Cargo
  dependency versions and declared licenses.

## V1 promotion gates

- [ ] Capture: 30-minute WGC/DXGI soak across selected DX11/DX12/Vulkan/OpenGL,
  multi-monitor/DPI, minimize/alt-tab, fullscreen, resize, and device loss.
- [ ] Control: supervised held-key fault injection, focus theft, UIPI mismatch,
  emergency-hotkey latency, watchdog latency, and optional gamepad neutralization.
- [ ] Recorder: supervised 10-minute gameplay Episode with complete timeline,
  source video, action/observation linkage, provenance, and replay.
- [ ] Dataset: at least the staged 5-hour quality-reviewed corpus, locked
  episode/session/player/game splits, licenses, and no test-game leakage.
- [ ] Models: artifact manifests, base-model/dataset licenses, offline metrics,
  latency benchmarks, and closed-loop results for motor, instruction, recovery,
  and reasoning-gate stages.
- [ ] Generalization: UGA-Bench Train A/B/C and held-out Game D results.
- [ ] Product: runtime, recorder, replay debugger, dataset schema, environment
  and skill SDKs, fast policy, GUI backend, examples, recipes, documentation,
  clean installation, and rollback test.
- [ ] Governance: repository license selected, source revision recorded, third-
  party notices verified, security review complete, and release manifest reports
  `releasable: true`.
