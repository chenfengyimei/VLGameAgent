"""Bounded, ordered recording work with atomic admission and bounded shutdown."""
from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from threading import Condition, Thread

from uga.core.artifact_limits import DEFAULT_ARTIFACT_LIMITS
from uga.core.errors import ContractViolation


class RecorderChannel:
    """One worker; capacities include both queued and in-flight work.

    Admission and accounting are atomic. A failed or timed-out channel is
    permanently unusable. It never silently replaces an accepted record. A
    stuck operation cannot be killed safely; close reports failure, detaches
    its daemon worker and leaves the caller's staging artifact unpublished.
    """

    def __init__(
        self, capacity: int = 1024, *,
        max_pending_bytes: int = DEFAULT_ARTIFACT_LIMITS.max_recorder_queue_bytes,
    ) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ContractViolation("recorder channel capacity must be positive")
        if type(max_pending_bytes) is not int or max_pending_bytes < 0:
            raise ContractViolation("recorder channel byte cap cannot be negative")
        self._capacity = capacity
        self._max_pending_bytes = max_pending_bytes
        self._pending_bytes = 0
        self._pending_entries = 0
        self._completed = 0
        self._rejected = 0
        self._discarded = 0
        self._failure: BaseException | None = None
        self._closed = False
        self._queue: deque[tuple[int, Callable[[], None]]] = deque()
        self._condition = Condition()
        self._thread = Thread(target=self._run, name="uga-recorder", daemon=True)
        self._thread.start()

    def submit(
        self, operation: Callable[[], None], timeout_s: float | None = None, *,
        weight_bytes: int = 0,
    ) -> None:
        if type(weight_bytes) is not int or weight_bytes < 0:
            raise ContractViolation("recorder record weight cannot be negative")
        if timeout_s is not None and (not math.isfinite(timeout_s) or timeout_s < 0):
            raise ContractViolation("recorder admission timeout must be finite and nonnegative")
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        with self._condition:
            while True:
                self._check_open()
                if self._pending_bytes + weight_bytes > self._max_pending_bytes:
                    self._rejected += 1
                    raise TimeoutError("recorder channel pending-byte limit reached")
                if self._pending_entries < self._capacity:
                    break
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    self._rejected += 1
                    raise TimeoutError("recorder channel backpressure timeout")
                self._condition.wait(remaining)
            # The worker cannot consume this entry before its weight is owned.
            self._pending_entries += 1
            self._pending_bytes += weight_bytes
            self._queue.append((weight_bytes, operation))
            self._condition.notify_all()

    def check_health(self) -> None:
        with self._condition:
            if self._failure is not None:
                raise RuntimeError("recorder worker failed") from self._failure

    def stats(self) -> dict[str, int]:
        with self._condition:
            return {
                "pending_entries": self._pending_entries,
                "pending_bytes": self._pending_bytes,
                "completed": self._completed,
                "rejected": self._rejected,
                "discarded": self._discarded,
                "failed": int(self._failure is not None),
                "max_pending_bytes": self._max_pending_bytes,
            }

    @property
    def drained(self) -> bool:
        with self._condition:
            return (
                self._closed and not self._thread.is_alive()
                and self._failure is None and self._pending_entries == 0
            )

    def close(self, timeout_s: float = 5.0) -> None:
        if not math.isfinite(timeout_s) or timeout_s < 0:
            raise ContractViolation("recorder close timeout must be finite and nonnegative")
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._thread.join(timeout_s)
        with self._condition:
            if self._thread.is_alive():
                self._fail_locked(TimeoutError("recorder shutdown deadline exceeded"))
            if self._failure is not None:
                raise RuntimeError(
                    "recorder worker failed; recording is incomplete"
                ) from self._failure

    def _check_open(self) -> None:
        if self._failure is not None:
            raise RuntimeError("recorder worker failed") from self._failure
        if self._closed:
            raise ContractViolation("recorder channel is closed")

    def _fail_locked(self, failure: BaseException) -> None:
        if self._failure is None:
            self._failure = failure
        self._closed = True
        while self._queue:
            weight, _ = self._queue.popleft()
            self._pending_bytes -= weight
            self._pending_entries -= 1
            self._discarded += 1
        self._condition.notify_all()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: bool(self._queue) or self._closed)
                if not self._queue:
                    return
                weight, operation = self._queue.popleft()
            failure: BaseException | None = None
            try:
                operation()
            except BaseException as exc:
                failure = exc
            with self._condition:
                self._pending_bytes -= weight
                self._pending_entries -= 1
                if failure is not None:
                    self._discarded += 1
                    self._fail_locked(failure)
                else:
                    self._completed += 1
                self._condition.notify_all()
                if self._failure is not None:
                    return
