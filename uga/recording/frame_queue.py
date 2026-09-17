"""Ordered recording with bounded admission; incomplete evidence never qualifies.

The in-flight frame consumes queue capacity. The sole daemon worker may be
abandoned on timeout; Python cannot safely kill a native encoder thread. After
failure the live runner must retain staging and must not publish the Episode.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Condition, Thread

from uga.capture.frame import Frame
from uga.core.errors import ContractViolation, FatalRuntimeError


class RecordingFailure(FatalRuntimeError):
    """Recording evidence is incomplete; operator intervention is required."""


@dataclass(frozen=True, slots=True)
class FrameQueueStats:
    accepted: int
    recorded: int
    pending: int
    pending_bytes: int
    peak_bytes: int
    failed: bool
    closed: bool


class FrameRecorderQueue:
    def __init__(
        self,
        record: Callable[[Frame], None],
        *,
        max_frames: int = 64,
        max_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        if type(max_frames) is not int or max_frames < 1:
            raise ContractViolation("recording capacity must be a positive integer")
        if type(max_bytes) is not int or max_bytes < 1:
            raise ContractViolation("recording byte capacity must be a positive integer")
        self._record = record
        self._max_frames, self._max_bytes = max_frames, max_bytes
        self._items: deque[Frame] = deque()
        self._condition = Condition()
        self._worker: Thread | None = None
        self._accepting = True
        self._failure: BaseException | None = None
        self._accepted = self._recorded = self._pending = self._bytes = self._peak = 0

    def submit(self, frame: Frame) -> None:
        """Nonblocking admission, serialized by the capture publication lock."""
        size = frame.buffer_handle.size_bytes
        with self._condition:
            self._check_locked()
            if not self._accepting:
                raise RecordingFailure("recording queue is closed")
            if self._pending >= self._max_frames or self._bytes + size > self._max_bytes:
                self._failure = RecordingFailure("recording queue capacity exhausted")
                self._accepting = False
                self._condition.notify_all()
                raise self._failure
            self._items.append(frame)
            self._pending += 1
            self._bytes += size
            self._peak = max(self._peak, self._bytes)
            self._accepted += 1
            if self._worker is None:
                self._worker = Thread(target=self._consume, name="uga-frame-recorder", daemon=True)
                self._worker.start()
            self._condition.notify()

    def _check_locked(self) -> None:
        if self._failure is not None:
            raise RecordingFailure(
                "frame recording failed; Episode remains incomplete"
            ) from self._failure

    def check(self) -> None:
        with self._condition:
            self._check_locked()

    def _consume(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: bool(self._items) or not self._accepting or self._failure is not None
                )
                if self._failure is not None:
                    self._items.clear()
                    return
                if not self._items:
                    return
                frame = self._items.popleft()
            try:
                self._record(frame)
            except BaseException as exc:
                with self._condition:
                    self._failure = exc
                    self._accepting = False
                    self._items.clear()
                    self._condition.notify_all()
                return
            with self._condition:
                self._recorded += 1
                self._pending -= 1
                self._bytes -= frame.buffer_handle.size_bytes
                self._condition.notify_all()

    def close(self, timeout_s: float = 5.0) -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ContractViolation("recording close timeout must be finite and positive")
        with self._condition:
            self._accepting = False
            self._condition.notify_all()
            worker = self._worker
        if worker is not None:
            worker.join(timeout_s)
            if worker.is_alive():
                with self._condition:
                    self._failure = RecordingFailure("recording drain exceeded its deadline")
                    self._items.clear()
                    self._condition.notify_all()
        self.check()

    @property
    def complete(self) -> bool:
        with self._condition:
            return (
                not self._accepting
                and self._failure is None
                and self._pending == 0
                and (self._worker is None or not self._worker.is_alive())
            )

    def stats(self) -> FrameQueueStats:
        with self._condition:
            return FrameQueueStats(
                self._accepted,
                self._recorded,
                self._pending,
                self._bytes,
                self._peak,
                self._failure is not None,
                not self._accepting,
            )
