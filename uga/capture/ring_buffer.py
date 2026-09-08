from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Condition, Lock

from uga.capture.frame import Frame
from uga.core.errors import ClockRegressionError, ContractViolation


@dataclass(frozen=True, slots=True)
class SequencedFrame:
    sequence: int
    frame: Frame


class FrameRingBuffer:
    """Bounded in-process history with O(1) access to the newest frame."""

    def __init__(self, capacity: int = 8) -> None:
        if capacity < 1:
            raise ContractViolation("frame ring capacity must be positive")
        self._frames: deque[SequencedFrame] = deque(maxlen=capacity)
        self._sequence = 0
        self._dropped = 0
        self._condition = Condition(Lock())

    def publish(self, frame: Frame) -> SequencedFrame:
        frame.validate()
        with self._condition:
            if self._frames and frame.capture_timestamp < self._frames[-1].frame.capture_timestamp:
                raise ClockRegressionError("frame capture timestamp regressed")
            if len(self._frames) == self._frames.maxlen:
                self._dropped += 1
            self._sequence += 1
            item = SequencedFrame(self._sequence, frame)
            self._frames.append(item)
            self._condition.notify_all()
            return item

    def latest(self) -> SequencedFrame | None:
        with self._condition:
            return self._frames[-1] if self._frames else None

    def snapshot(self) -> tuple[SequencedFrame, ...]:
        with self._condition:
            return tuple(self._frames)

    def wait_for_newer(
        self, after_sequence: int, timeout: float | None = None
    ) -> SequencedFrame | None:
        with self._condition:
            ready = self._condition.wait_for(
                lambda: bool(self._frames and self._frames[-1].sequence > after_sequence), timeout
            )
            return self._frames[-1] if ready else None

    @property
    def dropped(self) -> int:
        with self._condition:
            return self._dropped


class LatestFrameSlot:
    """Single-pending-item transport for policy and observation consumers."""

    def __init__(self) -> None:
        self._item: SequencedFrame | None = None
        self._replaced = 0
        self._lock = Lock()

    def offer(self, item: SequencedFrame) -> None:
        with self._lock:
            if self._item is not None:
                self._replaced += 1
            self._item = item

    def take(self) -> SequencedFrame | None:
        with self._lock:
            item = self._item
            self._item = None
            return item

    @property
    def replaced(self) -> int:
        with self._lock:
            return self._replaced
