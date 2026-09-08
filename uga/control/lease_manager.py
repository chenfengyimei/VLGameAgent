from __future__ import annotations

import uuid
from threading import Lock

from uga.control.lease import ControlLease, ControlMode, ControlOwner
from uga.core.errors import ContractViolation, LeaseDeniedError
from uga.time.clock import ClockBackend, UGATime


class ControlLeaseManager:
    """Single authority for expiring, generation-checked control ownership."""

    def __init__(self, clock: ClockBackend) -> None:
        self._clock = clock
        self._current: ControlLease | None = None
        self._generation = 0
        self._lock = Lock()

    def grant(
        self,
        owner: ControlOwner,
        mode: ControlMode,
        duration_ns: int,
        *,
        confidence: float,
        reason: str,
    ) -> ControlLease:
        if duration_ns <= 0:
            raise ContractViolation("lease duration must be positive")
        issued_at = self._clock.now()
        expires_at = UGATime(issued_at.value_ns + duration_ns)
        with self._lock:
            current = self._current
            if current is not None and not current.is_expired(issued_at) and current.owner > owner:
                raise LeaseDeniedError(
                    f"{owner.name} cannot preempt higher-priority {current.owner.name}"
                )
            self._generation += 1
            lease = ControlLease(
                lease_id=uuid.uuid4().hex,
                owner=owner,
                mode=mode,
                generation=self._generation,
                issued_at=issued_at,
                expires_at=expires_at,
                confidence=confidence,
                reason=reason,
            )
            self._current = lease
            return lease

    def current(self, now: UGATime | None = None) -> ControlLease | None:
        timestamp = now or self._clock.now()
        with self._lock:
            if self._current is not None and self._current.is_expired(timestamp):
                self._current = None
            return self._current

    def validate(self, lease: ControlLease, now: UGATime | None = None) -> bool:
        timestamp = now or self._clock.now()
        with self._lock:
            return (
                self._current == lease
                and not lease.is_expired(timestamp)
                and lease.generation == self._generation
            )

    def revoke(self, lease_id: str) -> bool:
        with self._lock:
            if self._current is None or self._current.lease_id != lease_id:
                return False
            self._current = None
            self._generation += 1
            return True

    def revoke_all(self) -> None:
        with self._lock:
            self._current = None
            self._generation += 1

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation
