from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Protocol

from uga.control.executor import InputExecutor
from uga.control.lease_manager import ControlLeaseManager
from uga.safety.focus_guard import AgentEnableState
from uga.time.clock import ClockBackend, UGATime


class QueueFlusher(Protocol):
    def flush(self) -> int: ...


class ShutdownCause(StrEnum):
    NORMAL_STOP = "normal_stop"
    EMERGENCY_HOTKEY = "emergency_hotkey"
    WATCHDOG_TIMEOUT = "watchdog_timeout"
    RUNTIME_FAILURE = "runtime_failure"


@dataclass(frozen=True, slots=True)
class SafetyTrip:
    cause: ShutdownCause
    occurred_at: UGATime
    flushed_actions: int
    cleanup_errors: tuple[str, ...] = ()


class SafetyShutdown:
    """Latched, idempotent fail-safe shared by watchdog and emergency stop."""

    def __init__(
        self,
        clock: ClockBackend,
        leases: ControlLeaseManager,
        queue: QueueFlusher,
        executor: InputExecutor,
        enabled: AgentEnableState,
    ) -> None:
        self._clock = clock
        self._leases = leases
        self._queue = queue
        self._executor = executor
        self._enabled = enabled
        self._trip: SafetyTrip | None = None
        self._lock = Lock()

    def trip(self, cause: ShutdownCause) -> SafetyTrip:
        with self._lock:
            if self._trip is not None:
                return self._trip
            occurred_at = self._clock.now()
            errors: list[str] = []
            self._enabled.set(False)
            # This shutdown owns the ordered flush/release below, so suppress the
            # scheduler's lease-loss callback and avoid duplicate backend writes.
            self._leases.revoke_all(notify=False)
            try:
                flushed = self._queue.flush()
            except Exception as exc:
                flushed = 0
                errors.append(f"queue flush failed: {exc}")
            try:
                self._executor.release_all()
            except Exception as exc:
                errors.append(f"input release failed: {exc}")
            self._trip = SafetyTrip(cause, occurred_at, flushed, tuple(errors))
            return self._trip

    @property
    def tripped(self) -> SafetyTrip | None:
        with self._lock:
            return self._trip
