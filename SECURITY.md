# Security Policy

## Supported environment policy

UGA development is restricted to offline/single-player games, private test
servers, developer-owned games, open-source games, and research sandboxes.
Competitive multiplayer automation, anti-cheat circumvention, and unattended
online match automation are not supported.

## Reporting

Do not publish a suspected vulnerability with a working exploit against a
third-party game or service. Report repository vulnerabilities privately to the
project owner until a disclosure channel is configured.

Physical input exists only behind the `InputExecutor`. It must fail closed on a
stale window identity, focus loss, integrity mismatch, expired lease, disabled
runtime, or emergency stop. Planner, GUI, policy, dashboard, dataset, and model
components may never call an OS input backend directly.

The dashboard and qualification tools must not accept untrusted remote command
traffic. Dashboard requests are restricted to the exact loopback IPv4 authority
chosen at bind time; browser command requests must also carry the matching
same-origin `Origin`. Native capture libraries are untrusted until their expected
SHA-256 is supplied and verified before loading, and every native session remains
bound to the same composite window identity before and after capture. Physical-
control and long-running fixture tests require a supervising operator. Model
checkpoints, datasets, videos, and game content are untrusted artifacts until
their hashes, provenance, licenses, and distribution rights have been reviewed.
