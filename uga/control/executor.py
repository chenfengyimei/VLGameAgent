from __future__ import annotations

import contextlib
from dataclasses import dataclass
from enum import StrEnum

from uga.control.input_backend import InputBackend
from uga.control.lease import ControlLease
from uga.control.lease_manager import ControlLeaseManager
from uga.control.physical import PhysicalAction
from uga.safety.focus_guard import FocusGuard, GuardReason
from uga.time.clock import ClockBackend, UGATime
from uga.windows.window_identity import WindowIdentity


class ExecutionReason(StrEnum):
    EXECUTED = "executed"
    NOT_YET_EFFECTIVE = "not_yet_effective"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    executed: bool
    reason: ExecutionReason | GuardReason
    executed_at: UGATime


class InputExecutor:
    """Sole component permitted to call an InputBackend."""

    def __init__(
        self,
        clock: ClockBackend,
        backend: InputBackend,
        guard: FocusGuard,
        leases: ControlLeaseManager,
    ) -> None:
        self._clock = clock
        self._backend = backend
        self._guard = guard
        self._leases = leases

    def execute(
        self, action: PhysicalAction, target: WindowIdentity, lease: ControlLease
    ) -> ExecutionResult:
        now = self._clock.now()
        if action.lifetime.is_expired(now):
            self._backend.release_all()
            return ExecutionResult(False, ExecutionReason.EXPIRED, now)
        if now < action.lifetime.effective_from:
            return ExecutionResult(False, ExecutionReason.NOT_YET_EFFECTIVE, now)
        guard = self._guard.check(target, lease)
        if not guard.allowed:
            self._backend.release_all()
            return ExecutionResult(False, guard.reason, now)
        try:
            submitted = self._leases.run_if_valid(lease, lambda: self._backend.submit(action))
        except BaseException:
            with contextlib.suppress(Exception):
                self._backend.release_all()
            raise
        if not submitted:
            self._backend.release_all()
            return ExecutionResult(False, GuardReason.LEASE_INVALID, now)
        return ExecutionResult(True, ExecutionReason.EXECUTED, now)

    def release_all(self) -> None:
        self._backend.release_all()
