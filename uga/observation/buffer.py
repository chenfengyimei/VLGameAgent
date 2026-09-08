from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

from uga.core.errors import ContractViolation
from uga.observation.schema import Observation


class ObservationRepresentation(StrEnum):
    HIGH_RESOLUTION = "high_resolution"
    LOW_RESOLUTION = "low_resolution"
    SUMMARY = "summary"


@dataclass(frozen=True, slots=True)
class TemporalObservation:
    observation: Observation
    representation: ObservationRepresentation


class TemporalObservationBuffer:
    """Bounded 4-8 item context with explicit mixed-resolution intent."""

    def __init__(self, capacity: int = 8, low_resolution_count: int = 3) -> None:
        if not 4 <= capacity <= 8:
            raise ContractViolation("temporal observation capacity must be 4 to 8")
        if not 0 <= low_resolution_count < capacity:
            raise ContractViolation("invalid low-resolution observation count")
        self._items: deque[Observation] = deque(maxlen=capacity)
        self._low_resolution_count = low_resolution_count
        self._lock = Lock()

    def append(self, observation: Observation) -> None:
        observation.validate()
        with self._lock:
            if self._items and observation.created_at < self._items[-1].created_at:
                raise ContractViolation("temporal observations cannot regress")
            self._items.append(observation)

    def latest(self) -> Observation | None:
        with self._lock:
            return self._items[-1] if self._items else None

    def context(self) -> tuple[TemporalObservation, ...]:
        with self._lock:
            items = tuple(self._items)
        result: list[TemporalObservation] = []
        recent_boundary = max(0, len(items) - 1 - self._low_resolution_count)
        for index, observation in enumerate(items):
            if index == len(items) - 1:
                representation = ObservationRepresentation.HIGH_RESOLUTION
            elif index >= recent_boundary:
                representation = ObservationRepresentation.LOW_RESOLUTION
            else:
                representation = ObservationRepresentation.SUMMARY
            result.append(TemporalObservation(observation, representation))
        return tuple(result)
