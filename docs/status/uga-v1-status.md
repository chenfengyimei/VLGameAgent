# UGA V1.1 delivery status

Updated: 2026-09-13 (grounded VLM live-smoke candidate `a9b2fb1`).

All planned issue surfaces UGA-001 through UGA-075 are represented in code,
configuration, tests, or release tooling. A requirements follow-up has also
added persistent Dataset Manifests, full offline metrics, adaptive policy
cadence, runtime telemetry files, executable qualification CLIs, hash-anchored
evidence, a developer-owned visual fixture, a modular realtime control loop,
and a loopback-only live dashboard. This is implementation coverage, not release
qualification.
Browser interactions are now compiled from strict TypeScript, and training
artifacts bind source Episode provenance plus checkpoint/input hashes.

| Range | Milestone | Implementation | External qualification |
|---|---|---|---|
| UGA-001–013 | Capture Foundation | Implemented; automated checks pass | Historical Fixture soak exists; current-revision, complete API/device-loss ledger entry pending |
| UGA-014–029 | Safe Control | Implemented; typed Fixture/UIPI aggregate rejects simulated evidence | Current-revision supervised focus/UIPI/hotkey/watchdog matrix pending; gamepad remains optional |
| UGA-030–034 | Recorder/Replay | Implemented; automated checks pass | Historical Fixture Episode exists; current-revision ledger entry pending |
| UGA-035–053 | Baseline Agent | Grounded single-action visual loop, OCR fusion, stale-result rejection, goal/effect verification, and bounded recovery implemented; automated checks pass | Local Qwen3-VL MuMu smoke passed; 200-sample offline and 100-Episode Fixture/MuMu matrices pending |
| UGA-054–070 | Dataset/Fast Policy | Five-stage recursive qualification and preflight binding implemented | Five-hour corpus and real GPU motor/instruction/recovery/reasoning-gate/DAgger runs pending |
| UGA-071–074 | Generalization/Packaging | Typed benchmark and source-bound build evidence implemented | Qualified-volume benchmark, clean-machine matrix, and final rebuilt bundle pending |
| UGA-075 | V1 release | Gate machinery implemented; MIT license present | Blocked: no final ledger/preflight, incomplete external gates, stale non-releasable bundle |

The authoritative promotion checklist is `RELEASE_CHECKLIST.md`; every release
bundle also contains a hashed `release-manifest.json` recording current status.
Qualification evidence uses `uga-qualify` and the contract in
`docs/contracts/qualification-evidence.md`.
The detailed audit and continuation plan is
`docs/reviews/post-glm-implementation-audit.md`.
The grounded visual-loop procedure and latest honest smoke boundary are recorded
in `docs/runbooks/vlm-closed-loop-qualification.md` and
`docs/reviews/vlm-closed-loop-live-smoke-2026-09-13.md`.
