# UGA V1 release qualification

Packaging must not be interpreted as release approval. V1 is releasable only
when every gate below has recorded evidence.

Runtime/data code remediation is tracked in the
[module delivery](docs/reviews/runtime-hardening-completion-2026-09-17.md).
Its regression tests do not close the live hardware, corpus or GPU gates below.
Previously checked smoke entries are historical, not automatically re-qualified
for every subsequent source revision.

## Development package evidence

- [x] Ruff, strict mypy, pytest, and Rust release compilation pass.
- [x] Local Qwen3-VL grounded-loop development smoke reaches Android Settings
  through one MuMu click and terminates on two-frame goal confirmation; capture
  p95/max gaps meet the 350/500 ms development limits.
- [ ] Grounded-loop reliability qualification includes the annotated 200-sample
  offline corpus, 100 Fixture/MuMu Episodes, injected loops, and 30-minute soak
  at the documented thresholds.
- [ ] Current audited revision builds wheel and source distribution in isolated
  PEP 517 environments.
- [ ] Current audited wheel installs into a clean virtual environment without dependency
  resolution, and the installed agent/dashboard console entries run.
- [ ] Current audited bundled launcher resolves the adjacent native capture DLL and runs the
  installed lifecycle smoke entry.
- [ ] Current audited package proves Fixture World headless goal reaches `COMPLETE`; installed capture,
  dataset, benchmark, and qualification commands are present.
- [ ] Current audited build emits a machine-readable inventory of exact Python, npm, and Cargo
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
  reasoning-gate, and DAgger stages.
- [ ] Generalization: UGA-Bench Train A/B/C and held-out Game D results.
- [ ] Product: runtime, recorder, replay debugger, dataset schema, environment
  and skill SDKs, fast policy, GUI backend, examples, recipes, documentation,
  clean installation, and rollback test.
- [ ] Governance: repository license selected, source revision recorded, third-
  party notices verified, security review complete, and release manifest reports
  `releasable: true`.
