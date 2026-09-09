from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock, Thread

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

    def force_trip(self) -> SafetyTrip | None:
        """Fail-closed response for a broken liveness probe; latches a runtime trip."""
        return self._shutdown.trip(ShutdownCause.RUNTIME_FAILURE)


class RuntimeWatchdogMonitor:
    """Independent poller that keeps watchdog enforcement alive during stalls."""

    def __init__(self, watchdog: RuntimeWatchdog, *, poll_interval_s: float = 0.1) -> None:
        if poll_interval_s <= 0:
            raise ContractViolation("watchdog poll interval must be positive")
        self._watchdog = watchdog
        self._poll_interval_s = poll_interval_s
        self._stop = Event()
        self._thread = Thread(target=self._run, name="uga-watchdog", daemon=True)

    def start(self) -> None:
        if self._thread.is_alive():
            return
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=max(1.0, self._poll_interval_s * 4))

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval_s):
            try:
                self._watchdog.check()
            except Exception:
                # A failed liveness probe must never silently disarm enforcement.
                self._watchdog.force_trip()
