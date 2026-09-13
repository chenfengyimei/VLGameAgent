from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

from uga.capture.frame import Frame
from uga.capture.ring_buffer import FrameRingBuffer, SequencedFrame
from uga.core.errors import CaptureTimeoutError, ContractViolation


class SynchronousFrameSource(Protocol):
    def capture(self) -> Frame: ...


@dataclass(frozen=True, slots=True)
class CaptureHubStats:
    accepted_frames: int
    primary_frames: int
    fallback_frames: int
    stale_source_frames: int
    consumer_skipped_frames: int
    p95_gap_ns: int
    max_gap_ns: int


class CaptureHub:
    """Continuously publishes one ordered frame timeline from primary/fallback sources.

    The primary source is capture-driven.  The fallback is sampled only after
    the accepted timeline has been quiet for ``fallback_after_s``. Consumers
    always receive the newest item and may skip obsolete frames, while the
    recorder callback receives every accepted frame before it becomes visible.
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
        consumer_timeout_s: float = 10.0,
    ) -> None:
        if not math.isfinite(fallback_after_s) or fallback_after_s <= 0:
            raise ContractViolation("capture fallback delay must be positive")
        if not math.isfinite(fallback_hz) or fallback_hz <= 0 or fallback_hz > 4.0:
            raise ContractViolation("capture fallback frequency must be in (0, 4]")
        if not math.isfinite(consumer_timeout_s) or consumer_timeout_s <= 0:
            raise ContractViolation("capture consumer timeout must be positive")
        self._primary = primary
        self._fallback = fallback
        self._frames = frames
        self._record_frame = record_frame
        self._fallback_after_s = fallback_after_s
        self._fallback_period_s = 1.0 / fallback_hz
        self._consumer_timeout_s = consumer_timeout_s
        self._publish_lock = Lock()
        self._stats_lock = Lock()
        self._started = asyncio.Event()
        self._last_publish_monotonic: float | None = None
        self._last_capture_timestamp_ns: int | None = None
        self._last_consumer_sequence = 0
        self._accepted = 0
        self._stale = 0
        self._consumer_skipped = 0
        self._sources: Counter[str] = Counter()
        self._gaps_ns: list[int] = []

    async def run(self, stop: asyncio.Event) -> None:
        self._started.set()
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._capture_primary(stop))
            if self._fallback is not None:
                tasks.create_task(self._capture_fallback(stop))

    async def capture_once(self) -> SequencedFrame:
        await self._started.wait()
        item = await asyncio.to_thread(
            self._frames.wait_for_newer,
            self._last_consumer_sequence,
            self._consumer_timeout_s,
        )
        if item is None:
            raise CaptureTimeoutError("capture hub produced no frame within its consumer budget")
        with self._stats_lock:
            skipped = max(0, item.sequence - self._last_consumer_sequence - 1)
            self._consumer_skipped += skipped
            self._last_consumer_sequence = item.sequence
        return item

    def stats(self) -> CaptureHubStats:
        with self._stats_lock:
            ordered = sorted(self._gaps_ns)
            p95_index = min(round((len(ordered) - 1) * 0.95), len(ordered) - 1)
            return CaptureHubStats(
                accepted_frames=self._accepted,
                primary_frames=self._sources["primary"],
                fallback_frames=self._sources["fallback"],
                stale_source_frames=self._stale,
                consumer_skipped_frames=self._consumer_skipped,
                p95_gap_ns=ordered[p95_index] if ordered else 0,
                max_gap_ns=max(ordered, default=0),
            )

    async def _capture_primary(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                frame = await asyncio.to_thread(self._primary.capture)
            except CaptureTimeoutError:
                await asyncio.sleep(0)
                continue
            await asyncio.to_thread(self._publish, frame, "primary")

    async def _capture_fallback(self, stop: asyncio.Event) -> None:
        assert self._fallback is not None
        started = time.monotonic()
        last_attempt: float | None = None
        while not stop.is_set():
            now = time.monotonic()
            last = self._last_publish_monotonic
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
            last = self._last_publish_monotonic
            if last is not None and time.monotonic() - last < self._fallback_after_s:
                continue
            last_attempt = time.monotonic()
            try:
                frame = await asyncio.to_thread(self._fallback.capture)
            except CaptureTimeoutError:
                continue
            await asyncio.to_thread(self._publish, frame, "fallback")

    def _publish(self, frame: Frame, source: str) -> None:
        with self._publish_lock:
            timestamp_ns = frame.capture_timestamp.value_ns
            latest = self._frames.latest()
            if latest is not None and frame.capture_timestamp < latest.frame.capture_timestamp:
                with self._stats_lock:
                    self._stale += 1
                return
            if self._record_frame is not None:
                self._record_frame(frame)
            self._frames.publish(frame)
            now = time.monotonic()
            with self._stats_lock:
                if self._last_capture_timestamp_ns is not None:
                    self._gaps_ns.append(timestamp_ns - self._last_capture_timestamp_ns)
                self._last_capture_timestamp_ns = timestamp_ns
                self._last_publish_monotonic = now
                self._accepted += 1
                self._sources[source] += 1
