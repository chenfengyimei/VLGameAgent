from __future__ import annotations

from enum import StrEnum

from uga.core.errors import ContractViolation


class LifecycleState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    SHUTDOWN = "shutdown"


_ALLOWED: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.CREATED: frozenset({LifecycleState.RUNNING, LifecycleState.SHUTDOWN}),
    LifecycleState.RUNNING: frozenset(
        {LifecycleState.PAUSED, LifecycleState.STOPPED, LifecycleState.SHUTDOWN}
    ),
    LifecycleState.PAUSED: frozenset(
        {LifecycleState.RUNNING, LifecycleState.STOPPED, LifecycleState.SHUTDOWN}
    ),
    LifecycleState.STOPPED: frozenset({LifecycleState.RUNNING, LifecycleState.SHUTDOWN}),
    LifecycleState.SHUTDOWN: frozenset(),
}


def validate_transition(current: LifecycleState, target: LifecycleState) -> None:
    if target not in _ALLOWED[current]:
        raise ContractViolation(f"invalid lifecycle transition: {current} -> {target}")
