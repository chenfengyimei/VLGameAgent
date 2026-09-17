from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from uga.capture.frame import Frame
from uga.capture.ring_buffer import FrameRingBuffer, SequencedFrame
from uga.core.deadline import BoundedWorker, Deadline, DeadlineExceeded
from uga.core.errors import BackendUnavailableError, CaptureTimeoutError, ContractViolation
from uga.recording.frame_queue import FrameQueueStats, FrameRecorderQueue


class SynchronousFrameSource(Protocol):
    def capture(self) -> Frame: ...


# F16: the rolling gap window size — long runs accumulate a bounded history
# instead of an ever-growing list that stats() must re-sort.
_GAP_WINDOW = 512


@dataclass(frozen=True, slots=True)
class CaptureHubStats:
    accepted_frames: int
    primary_frames: int
    fallback_frames: int
    primary_errors: int
    fallback_errors: int
    stale_source_frames: int
    consumer_skipped_frames: int
    p95_gap_ns: int
    max_gap_ns: int


class CaptureHub:
    """Continuously publishes one ordered frame timeline from primary/fallback sources.

    The primary source is capture-driven.  The fallback is sampled only after
    the accepted timeline has been quiet for ``fallback_after_s``. Consumers
    always receive the newest item and may skip obsolete frames.  The recorder
    callback is queued in publication order with independent frame and byte
    budgets. Overflow stops recording instead of silently dropping evidence
    or blocking the control plane.
    """

    records_frames = True

    def __init__(
        self,
        *,
        primary: SynchronousFrameSource,
        frames: FrameRingBuffer,
        fallback: SynchronousFrameSource | None = None,
        record_frame: Callable[[Frame], None] | None = None,
        fallback_after_s: float = 0.25,
        fallback_hz: float = 4.0,
        primary_hz: float | None = None,
        consumer_timeout_s: float = 10.0,
        capture_operation_timeout_s: float = 5.0,
        source_error_backoff_s: float = 0.05,
        recording_max_frames: int = 64,
        recording_max_bytes: int = 256 * 1024 * 1024,
        recording_close_timeout_s: float = 5.0,
    ) -> None:
        if not math.isfinite(fallback_after_s) or fallback_after_s <= 0:
            raise ContractViolation("capture fallback delay must be positive")
        if not math.isfinite(fallback_hz) or fallback_hz <= 0 or fallback_hz > 4.0:
            raise ContractViolation("capture fallback frequency must be in (0, 4]")
        if primary_hz is not None and (
            not math.isfinite(primary_hz) or primary_hz <= 0 or primary_hz > 60.0
        ):
            raise ContractViolation("capture primary frequency must be in (0, 60]")
        if not math.isfinite(consumer_timeout_s) or consumer_timeout_s <= 0:
            raise ContractViolation("capture consumer timeout must be positive")
        if (
            not math.isfinite(source_error_backoff_s)
            or source_error_backoff_s <= 0
            or source_error_backoff_s > 1.0
        ):
            raise ContractViolation("capture source error backoff must be in (0, 1]")
        Deadline.after(capture_operation_timeout_s)
        self._capture_operation_timeout_s = capture_operation_timeout_s
        self._primary_worker = BoundedWorker()
        self._fallback_worker = BoundedWorker()
        self._primary = primary
        self._fallback = fallback
        self._frames = frames
        if not math.isfinite(recording_close_timeout_s) or recording_close_timeout_s <= 0:
            raise ContractViolation("recording close timeout must be positive")
        self._recording_close_timeout_s = recording_close_timeout_s
        self._recording = (
            None if record_frame is None else FrameRecorderQueue(
                record_frame, max_frames=recording_max_frames, max_bytes=recording_max_bytes
            )
        )
        self._fallback_after_s = fallback_after_s
        self._fallback_period_s = 1.0 / fallback_hz
        self._primary_period_s = None if primary_hz is None else 1.0 / primary_hz
        self._consumer_timeout_s = consumer_timeout_s
        self._source_error_backoff_s = source_error_backoff_s
        self._publish_lock = Lock()
        self._stats_lock = Lock()
        self._started = asyncio.Event()
        # Scheduling follows when the accepted image was sampled, not when its
        # potentially expensive CPU copy finished publishing. Otherwise a slow
        # GDI copy is added to every 250 ms heartbeat period and can inflate the
        # recorded capture-timestamp gap beyond the qualification ceiling.
        self._last_frame_monotonic: float | None = None
        self._last_capture_timestamp_ns: int | None = None
        self._last_consumer_sequence = 0
        self._accepted = 0
        self._stale = 0
        self._consumer_skipped = 0
        self._sources: Counter[str] = Counter()
        self._source_errors: Counter[str] = Counter()
        # F16: bounded rolling gap window instead of an unbounded list. The
        # ring holds the most recent samples for the P95 estimate; the all-time
        # max and count are O(1) accumulators so stats() never sorts or walks
        # a history that grows with the run.
        self._gaps_ns: deque[int] = deque(maxlen=_GAP_WINDOW)
        self._gap_max_ns = 0
        self._gap_count = 0

    async def run(self, stop: asyncio.Event) -> None:
        self._started.set()
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self._capture_primary(stop))
                if self._fallback is not None:
                    tasks.create_task(self._capture_fallback(stop))
                if self._recording is not None:
                    tasks.create_task(self._monitor_recording(stop))
        finally:
            if self._recording is not None:
                await asyncio.to_thread(self._recording.close, self._recording_close_timeout_s)

    async def _monitor_recording(self, stop: asyncio.Event) -> None:
        assert self._recording is not None
        while not stop.is_set():
            self._recording.check()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=0.05)

    @property
    def recording_complete(self) -> bool:
        return self._recording is None or self._recording.complete

    def recording_stats(self) -> FrameQueueStats | None:
        return None if self._recording is None else self._recording.stats()

    async def capture_once(self) -> SequencedFrame:
        await self._started.wait()
        deadline = time.monotonic() + self._consumer_timeout_s
        while True:
            item = self._frames.latest()
            if item is not None and item.sequence > self._last_consumer_sequence:
                break
            if time.monotonic() >= deadline:
                raise CaptureTimeoutError(
                    "capture hub produced no frame within its consumer budget"
                )
            await asyncio.sleep(.005)
        with self._stats_lock:
            skipped = max(0, item.sequence - self._last_consumer_sequence - 1)
            self._consumer_skipped += skipped
            self._last_consumer_sequence = item.sequence
        return item

    def stats(self) -> CaptureHubStats:
        with self._stats_lock:
            # F16: bounded snapshot read — copy the small rolling window, then
            # sort the COPY outside the lock so a stats consumer never holds
            # the capture lock during the sort and never walks an unbounded
            # history.
            window = sorted(self._gaps_ns)
            p95_index = min(round((len(window) - 1) * 0.95), len(window) - 1)
            return CaptureHubStats(
                accepted_frames=self._accepted,
                primary_frames=self._sources["primary"],
                fallback_frames=self._sources["fallback"],
                primary_errors=self._source_errors["primary"],
                fallback_errors=self._source_errors["fallback"],
                stale_source_frames=self._stale,
                consumer_skipped_frames=self._consumer_skipped,
                p95_gap_ns=window[p95_index] if window else 0,
                max_gap_ns=self._gap_max_ns,
            )

    async def _capture_primary(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            attempt_started = time.monotonic()
            try:
                frame = await self._primary_worker.run(
                    Deadline.after(self._capture_operation_timeout_s, stop.is_set),
                    self._primary.capture,
                )
            except DeadlineExceeded:
                raise  # abandoned driver workers must never be retried
            except CaptureTimeoutError:
                await asyncio.sleep(0)
                continue
            except (BackendUnavailableError, ContractViolation):
                # A transient WGC session failure must not cancel the fallback
                # heartbeat (or the whole AgentLoop TaskGroup). Keep retrying
                # the primary with bounded backoff while GDI owns the timeline.
                with self._stats_lock:
                    self._source_errors["primary"] += 1
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop.wait(), timeout=self._source_error_backoff_s
                    )
                continue
            if stop.is_set():
                break
            self._publish(frame, "primary", time.monotonic())
            if self._primary_period_s is not None:
                delay = attempt_started + self._primary_period_s - time.monotonic()
                if delay > 0:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), timeout=delay)

    async def _capture_fallback(self, stop: asyncio.Event) -> None:
        assert self._fallback is not None
        started = time.monotonic()
        last_attempt: float | None = None
        while not stop.is_set():
            now = time.monotonic()
            last = self._last_frame_monotonic
            timeline_due = (last if last is not None else started) + self._fallback_after_s
            rate_due = (
                last_attempt + self._fallback_period_s
                if last_attempt is not None
                else timeline_due
            )
            delay = max(timeline_due, rate_due) - now
            if delay > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                continue

            # The primary may have published while this task was waking. Recheck
            # the shared timeline before spending a fallback capture.
            last = self._last_frame_monotonic
            if last is not None and time.monotonic() - last < self._fallback_after_s:
                continue
            attempt_started = time.monotonic()
            try:
                frame = await self._fallback_worker.run(
                    Deadline.after(self._capture_operation_timeout_s, stop.is_set),
                    self._fallback.capture,
                )
            except DeadlineExceeded:
                raise
            except (CaptureTimeoutError, BackendUnavailableError, ContractViolation):
                # A failed heartbeat did not publish a frame and therefore does
                # not consume the 4 Hz output budget.  ContractViolation covers
                # a stale/recreated target window (invalid HWND): keep the
                # timeline alive instead of killing the whole process.
                with self._stats_lock:
                    self._source_errors["fallback"] += 1
                await asyncio.sleep(min(0.05, self._fallback_period_s / 10.0))
                continue
            last_attempt = attempt_started
            if stop.is_set():
                break
            self._publish(frame, "fallback", attempt_started)

    def _publish(self, frame: Frame, source: str, sampled_monotonic: float) -> None:
        with self._publish_lock:
            timestamp_ns = frame.capture_timestamp.value_ns
            latest = self._frames.latest()
            if latest is not None and frame.capture_timestamp < latest.frame.capture_timestamp:
                with self._stats_lock:
                    self._stale += 1
                return
            if self._recording is not None:
                self._recording.submit(frame)
            self._frames.publish(frame)
            with self._stats_lock:
                if self._last_capture_timestamp_ns is not None:
                    gap_ns = timestamp_ns - self._last_capture_timestamp_ns
                    self._gaps_ns.append(gap_ns)
                    if gap_ns > self._gap_max_ns:
                        self._gap_max_ns = gap_ns
                    self._gap_count += 1
                self._last_capture_timestamp_ns = timestamp_ns
                self._last_frame_monotonic = sampled_monotonic
                self._accepted += 1
                self._sources[source] += 1
