from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from uga.core.errors import ContractViolation
from uga.safety.shutdown import SafetyShutdown, SafetyTrip, ShutdownCause
from uga.time.clock import ClockBackend, UGATime


@dataclass(frozen=True, slots=True)
class WatchdogStatus:
    last_heartbeat: UGATime
    deadline: UGATime
    trip: SafetyTrip | None


class RuntimeWatchdog:
    """Monotonic heartbeat watchdog; once tripped it remains fail-closed."""

    def __init__(self, clock: ClockBackend, shutdown: SafetyShutdown, timeout_ns: int) -> None:
        if timeout_ns <= 0:
            raise ContractViolation("watchdog timeout must be positive")
        self._clock = clock
        self._shutdown = shutdown
        self._timeout_ns = timeout_ns
        self._last_heartbeat = clock.now()
        self._lock = Lock()

    def heartbeat(self) -> bool:
        with self._lock:
            if self._shutdown.tripped is not None:
                return False
            self._last_heartbeat = self._clock.now()
            return True

    def check(self) -> WatchdogStatus:
        now = self._clock.now()
        with self._lock:
            deadline = UGATime(self._last_heartbeat.value_ns + self._timeout_ns)
            trip = self._shutdown.tripped
            if trip is None and now > deadline:
                trip = self._shutdown.trip(ShutdownCause.WATCHDOG_TIMEOUT)
            return WatchdogStatus(self._last_heartbeat, deadline, trip)
