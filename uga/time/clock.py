from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable

from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin

INT64_MAX = 2**63 - 1


@dataclass(frozen=True, order=True, slots=True)
class UGATime(VersionedMixin):
    """A timestamp on the process-wide monotonic timeline."""

    SCHEMA_NAME: ClassVar[str] = "uga.time"
    value_ns: int

    def validate(self) -> None:
        if isinstance(self.value_ns, bool) or not isinstance(self.value_ns, int):
            raise ContractViolation("UGATime must contain an integer")
        if not 0 <= self.value_ns <= INT64_MAX:
            raise ContractViolation("UGATime must be a non-negative int64 nanosecond value")

    def __post_init__(self) -> None:
        self.validate()


@runtime_checkable
class ClockBackend(Protocol):
    """The only source of online-control timestamps."""

    def now(self) -> UGATime: ...


class PerfCounterClock:
    """Monotonic high-resolution clock; CPython maps this to QPC on Windows."""

    def now(self) -> UGATime:
        return UGATime(time.perf_counter_ns())


class ManualClock:
    """Deterministic clock for contract tests and replay."""

    def __init__(self, initial_ns: int = 0) -> None:
        self._now = UGATime(initial_ns)

    def now(self) -> UGATime:
        return self._now

    def set(self, value_ns: int) -> UGATime:
        self._now = UGATime(value_ns)
        return self._now

    def advance(self, delta_ns: int) -> UGATime:
        if delta_ns < 0:
            raise ContractViolation("manual clock cannot advance by a negative duration")
        return self.set(self._now.value_ns + delta_ns)
