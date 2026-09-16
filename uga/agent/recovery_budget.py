"""Run-level recovery budget: one accounting across supervisor rebuilds.

The CLI's ``--max-recoveries`` must bound EVERY failure recovery of a run —
high-resolution re-probes as well as visual back exits.  The budget is owned
by the composition root and survives supervisor rebuilds in continuous mode:
a fresh supervisor instance can never reset the accounting (F05/F06).
``max_recoveries=0`` disables every recovery, including BACK, which used to
bypass the budget by posing as an ordinary state transition.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

from uga.core.errors import ContractViolation


class RecoveryKind(StrEnum):
    HIGH_RESOLUTION = "high_resolution"
    BACK = "back"


@dataclass(frozen=True, slots=True)
class RecoveryBudgetState:
    limit: int
    consumed: int
    exhausted: bool


class RecoveryBudget:
    """Fail-closed counter owned by the composition root.

    ``limit=0`` refuses every recovery; ``limit=1`` allows exactly one of any
    kind for the whole run; consumption is idempotent per attempt and never
    resets while the run lives.
    """

    def __init__(self, limit: int) -> None:
        if limit < 0:
            raise ContractViolation("recovery budget limit cannot be negative")
        self._limit = limit
        self._consumed = 0
        self._lock = Lock()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def consumed(self) -> int:
        with self._lock:
            return self._consumed

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return self._consumed >= self._limit

    def allow(self, kind: RecoveryKind) -> bool:
        del kind  # every kind draws from the same run-level budget
        with self._lock:
            return self._consumed < self._limit

    def consume(self, kind: RecoveryKind) -> bool:
        del kind
        with self._lock:
            if self._consumed >= self._limit:
                return False
            self._consumed += 1
            return True

    def state(self) -> RecoveryBudgetState:
        with self._lock:
            return RecoveryBudgetState(
                limit=self._limit,
                consumed=self._consumed,
                exhausted=self._consumed >= self._limit,
            )
