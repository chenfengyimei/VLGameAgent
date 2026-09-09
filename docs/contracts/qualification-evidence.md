# Qualification evidence contract

UGA V1 promotion is evidence-driven. A status string in a checklist is not
sufficient evidence. `uga-qualify` maintains a versioned ledger containing all
required gates, a traceable source revision, evidence summaries, and SHA-256
digests of supporting artifacts.

A passed gate must reference at least one artifact beneath the evidence root.
Loading or displaying a ledger re-hashes every referenced file. A changed or
missing file invalidates verification. The repository-license gate additionally
requires a `LICENSE*` artifact. Placeholder revisions such as
`workspace-unversioned` can never yield a releasable manifest.

The required gates cover automated tests, package construction and installation,
capture soak, supervised control hardware tests, ten-minute Recorder/Replay,
the licensed five-hour dataset milestone, model training, four-game
generalization, and repository licensing/governance.

Typical ledger operations:

```powershell
uga-qualify init runs/qualification-v1 --source-revision <commit>
uga-qualify record runs/qualification-v1 capture-soak passed `
  --evidence "30-minute fixture and game capture matrix passed" `
  --artifact capture/soak-report.json
uga-qualify status runs/qualification-v1
uga-qualify preflight runs/qualification-v1 --project-root . `
  --training-artifact runs/qualification-v1/models/fixture-motor-v1/training-artifact.json `
  --dataset-manifest data/datasets/v1/manifest.json `
  --dataset-root data/datasets/v1
uga-qualify manifest runs/qualification-v1 dist/<bundle> --version 1.0.0
```

The final command verifies evidence again before creating a release manifest.
`preflight` is read-only: it checks Git HEAD alignment, repository licensing,
the verified training-artifact binding, Dataset artifact hashes and explicit
distribution/commercial permissions, five-hour volume, the Train A/B/C plus
locked-test-game shape, GPU discovery, and every ledger status. It does not
execute tests or turn incomplete gates into passed gates.
Each development bundle also hashes `third-party-inventory.json`, generated from
the installed Python packages, locked Cargo graph, and locked npm UI build graph.
Known license declarations are necessary but do not replace human legal review.
