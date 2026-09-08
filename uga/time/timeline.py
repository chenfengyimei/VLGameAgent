from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from uga.core.errors import ClockRegressionError, ContractViolation
from uga.time.clock import ClockBackend, UGATime


@dataclass(frozen=True, slots=True)
class TimelinePoint:
    sequence: int
    label: str
    timestamp: UGATime


class MonotonicTimeline:
    """Serializes timestamps and fails closed on clock regression."""

    def __init__(self, clock: ClockBackend) -> None:
        self._clock = clock
        self._last: UGATime | None = None
        self._sequence = 0
        self._lock = Lock()

    def stamp(self, label: str) -> TimelinePoint:
        if not label.strip():
            raise ContractViolation("timeline labels cannot be empty")
        current = self._clock.now()
        with self._lock:
            if self._last is not None and current < self._last:
                raise ClockRegressionError(
                    f"clock regressed from {self._last.value_ns} to {current.value_ns}"
                )
            self._sequence += 1
            self._last = current
            return TimelinePoint(self._sequence, label, current)

    @property
    def last(self) -> UGATime | None:
        with self._lock:
            return self._last
