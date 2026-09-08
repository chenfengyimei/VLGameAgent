# Baseline agent contracts

An Observation carries the latest frame descriptor, one to eight ordered frame
references, confirmed mode, goal/subgoal, recent actions/events, inferred belief,
visible text, active skill, lease, and latency budget. Frame envelopes contain
only opaque buffer descriptors; pixel payloads never leak into observation JSON.

Temporal context is bounded to four through eight observations. The newest is
marked high-resolution, recent history low-resolution, and older context a
summary/keyframe representation. `BeliefState` contains only agent inference.

Game profiles declare process names, capture preference, explicit control
bindings, camera behavior, capabilities, safety manifest, and capability level.
Generic adaptation refuses unsafe manifests and unconfirmed bindings. It emits
canonical-to-physical proposals only and never calls an input backend.

Mode changes require confidence, consecutive confirmations, and a minimum hold.
The router does not own or grant control leases. Planners return a fixed schema
and cannot return physical input. Skills are versioned, mode-scoped, registered,
and return only semantic or canonical requests. TaskGraph enforces dependencies,
retry limits, valid transitions, and acyclicity.

GUI providers return normalized GUI actions. `GuiControlBridge` performs the
explicit normalized/image/client/physical transform and produces a GUI lease-
bound proposal. Text uses Unicode keyboard events; no keyboard-layout guessing
occurs in the GUI layer.
