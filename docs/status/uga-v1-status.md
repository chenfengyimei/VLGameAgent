# UGA V1.1 delivery status

Updated: 2026-09-15 (direct-use candidate `79742a4`).

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
| UGA-001–013 | Capture Foundation | Implemented; WGC/DXGI/GDI failover and geometry checks pass | `d6da6d4` 30-minute Fixture evidence passed; `79742a4` 18-second hardware smoke passed |
| UGA-014–029 | Safe Control | Implemented; focus, lease, hotkey, watchdog, held-key and obstruction guards pass | `79742a4` supervised smoke passed every exercised check; real elevated-target UIPI probe remains unqualified |
| UGA-030–034 | Recorder/Replay | Implemented; checksums, Parquet, quality and replay pass | `d6da6d4` 30-minute recording and `79742a4` short recording were accepted with no findings |
| UGA-035–053 | Baseline Agent | Grounded single-action visual loop, OCR fusion, stale-result rejection, goal/effect verification, bounded recovery and live dashboard implemented | `d6da6d4` local Qwen3-VL offline-200, MuMu 20×5 plus loop, and 30-minute soak passed; Agent runtime is unchanged by `79742a4` |
| UGA-054–070 | Dataset/Fast Policy | Dataset, deterministic motor and five-stage recursive evidence contracts implemented | Five-hour corpus and real GPU motor/instruction/recovery/reasoning-gate/DAgger evidence were explicitly not completed |
| UGA-071–074 | Generalization/Packaging | Typed benchmark plus source-bound build evidence implemented | `79742a4` clean wheel/sdist/native bundle passed all 14 build checks; release-volume trained-policy benchmark remains unqualified |
| UGA-075 | V1 release | Direct-use development bundle, MIT license, tutorials and diagnostics are available | Not release-qualified: UIPI, five-hour dataset, five-stage training and trained-policy generalization gates remain open |

The authoritative promotion checklist is `RELEASE_CHECKLIST.md`; every release
bundle also contains a hashed `release-manifest.json` recording current status.
Qualification evidence uses `uga-qualify` and the contract in
`docs/contracts/qualification-evidence.md`.
The detailed audit and continuation plan is
`docs/reviews/post-glm-implementation-audit.md`.
The grounded visual-loop procedure and latest honest smoke boundary are recorded
in `docs/runbooks/vlm-closed-loop-qualification.md` and
`docs/reviews/vlm-closed-loop-live-smoke-2026-09-13.md`.
The final direct-use audit, including the exact evidence boundary, is in
`docs/reviews/final-usability-review-2026-09-15.md`.
