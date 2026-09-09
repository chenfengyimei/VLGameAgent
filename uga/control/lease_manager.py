from __future__ import annotations

import uuid
from collections.abc import Callable
from threading import Lock, RLock

from uga.control.lease import ControlLease, ControlMode, ControlOwner
from uga.core.errors import ContractViolation, LeaseDeniedError
from uga.time.clock import ClockBackend, UGATime


class ControlLeaseManager:
    """Single authority for expiring, generation-checked control ownership."""

    def __init__(self, clock: ClockBackend) -> None:
        self._clock = clock
        self._current: ControlLease | None = None
        self._generation = 0
        self._authority_loss_handlers: list[Callable[[], object]] = []
        self._lock = Lock()
        self._transition_lock = RLock()

    def register_authority_loss_handler(self, handler: Callable[[], object]) -> None:
        """Register fail-closed cleanup invoked after control authority is lost."""
        with self._transition_lock, self._lock:
            if handler not in self._authority_loss_handlers:
                self._authority_loss_handlers.append(handler)

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
        with self._transition_lock:
            issued_at = self._clock.now()
            expires_at = UGATime(issued_at.value_ns + duration_ns)
            with self._lock:
                current = self._current
                if (
                    current is not None
                    and not current.is_expired(issued_at)
                    and current.owner > owner
                ):
                    raise LeaseDeniedError(
                        f"{owner.name} cannot preempt higher-priority {current.owner.name}"
                    )
                replaced = current is not None
                if replaced:
                    self._current = None
                    self._generation += 1
            if replaced:
                self._notify_authority_loss()
            with self._lock:
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
        with self._transition_lock:
            timestamp = now or self._clock.now()
            expired = False
            with self._lock:
                if self._current is not None and self._current.is_expired(timestamp):
                    self._current = None
                    self._generation += 1
                    expired = True
                current = self._current
            if expired:
                self._notify_authority_loss()
            return current

    def validate(self, lease: ControlLease, now: UGATime | None = None) -> bool:
        with self._transition_lock:
            timestamp = now or self._clock.now()
            expired = False
            with self._lock:
                valid = (
                    self._current == lease
                    and not lease.is_expired(timestamp)
                    and lease.generation == self._generation
                )
                if self._current == lease and lease.is_expired(timestamp):
                    self._current = None
                    self._generation += 1
                    expired = True
            if expired:
                self._notify_authority_loss()
            return valid

    def run_if_valid(self, lease: ControlLease, operation: Callable[[], None]) -> bool:
        """Run one input write atomically with final lease validation."""
        with self._transition_lock:
            timestamp = self._clock.now()
            expired = False
            with self._lock:
                valid = (
                    self._current == lease
                    and not lease.is_expired(timestamp)
                    and lease.generation == self._generation
                )
                if valid:
                    operation()
                    return True
                if self._current == lease and lease.is_expired(timestamp):
                    self._current = None
                    self._generation += 1
                    expired = True
            if expired:
                self._notify_authority_loss()
            return False

    def revoke(self, lease_id: str) -> bool:
        with self._transition_lock:
            with self._lock:
                if self._current is None or self._current.lease_id != lease_id:
                    return False
                self._current = None
                self._generation += 1
            self._notify_authority_loss()
            return True

    def revoke_all(self, *, notify: bool = True) -> None:
        with self._transition_lock:
            with self._lock:
                had_authority = self._current is not None
                self._current = None
                self._generation += 1
            if had_authority and notify:
                self._notify_authority_loss()

    @property
    def generation(self) -> int:
        with self._transition_lock, self._lock:
            return self._generation

    def _notify_authority_loss(self) -> None:
        with self._lock:
            handlers = tuple(self._authority_loss_handlers)
        failure: BaseException | None = None
        for handler in handlers:
            try:
                handler()
            except BaseException as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure
