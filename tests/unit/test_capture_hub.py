from __future__ import annotations

import asyncio
import threading
import time
import unittest
from dataclasses import replace

from tests.helpers import frame
from uga.capture.frame import Frame
from uga.capture.hub import CaptureHub
from uga.capture.ring_buffer import FrameRingBuffer
from uga.core.errors import BackendUnavailableError, CaptureTimeoutError
from uga.time.clock import UGATime


class _Source:
    def __init__(self, *, period_s: float = 0.01, timeouts: bool = False) -> None:
        self.period_s = period_s
        self.timeouts = timeouts
        self.count = 0
        self._lock = threading.Lock()

    def capture(self) -> Frame:
        time.sleep(self.period_s)
        if self.timeouts:
            raise CaptureTimeoutError("fixture timeout")
        with self._lock:
            self.count += 1
            count = self.count
        return replace(
            frame(count, timestamp_ns=count * 10_000_000),
            frame_id=f"source-{count}",
            capture_timestamp=UGATime(count * 10_000_000),
        )


class _TransientFallback(_Source):
    def __init__(self) -> None:
        super().__init__(period_s=0.001)
        self.attempts = 0

    def capture(self) -> Frame:
        self.attempts += 1
        if self.attempts == 1:
            raise CaptureTimeoutError("transient fixture timeout")
        return super().capture()


class _UnavailableSource(_Source):
    def capture(self) -> Frame:
        self.count += 1
        raise BackendUnavailableError("transient fixture backend failure")


class _TimestampedSlowSource(_Source):
    """A GDI-like source whose timestamp precedes a costly pixel copy."""

    def capture(self) -> Frame:
        sampled_ns = time.perf_counter_ns()
        time.sleep(self.period_s)
        with self._lock:
            self.count += 1
            count = self.count
        return replace(
            frame(count, timestamp_ns=sampled_ns),
            frame_id=f"slow-{count}",
            capture_timestamp=UGATime(sampled_ns),
        )


class CaptureHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_continues_while_consumer_is_slow(self) -> None:
        source = _Source()
        frames = FrameRingBuffer(capacity=64)
        recorded: list[str] = []
        hub = CaptureHub(
            primary=source,
            frames=frames,
            record_frame=lambda item: recorded.append(item.frame_id),
            consumer_timeout_s=1.0,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(hub.run(stop))
        first = await hub.capture_once()

        await asyncio.sleep(0.12)  # represents a slow in-flight model request
        newest = await hub.capture_once()
        stop.set()
        await task

        self.assertGreaterEqual(newest.sequence - first.sequence, 5)
        self.assertEqual(recorded, [item.frame.frame_id for item in frames.snapshot()])
        self.assertGreater(hub.stats().consumer_skipped_frames, 0)

    async def test_fallback_only_runs_after_primary_timeline_stalls(self) -> None:
        primary = _Source(period_s=0.005, timeouts=True)
        fallback = _Source(period_s=0.001)
        hub = CaptureHub(
            primary=primary,
            fallback=fallback,
            frames=FrameRingBuffer(),
            fallback_after_s=0.02,
            fallback_hz=4.0,
            consumer_timeout_s=1.0,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(hub.run(stop))

        started = time.monotonic()
        item = await hub.capture_once()
        elapsed = time.monotonic() - started
        stop.set()
        await task

        self.assertTrue(item.frame.frame_id.startswith("source-"))
        self.assertEqual(hub.stats().primary_frames, 0)
        self.assertGreaterEqual(hub.stats().fallback_frames, 1)
        self.assertLess(elapsed, 0.15)

    async def test_backend_failure_keeps_fallback_alive_and_is_counted(self) -> None:
        primary = _UnavailableSource(period_s=0)
        fallback = _Source(period_s=0.001)
        hub = CaptureHub(
            primary=primary,
            fallback=fallback,
            frames=FrameRingBuffer(),
            fallback_after_s=0.02,
            fallback_hz=4.0,
            consumer_timeout_s=1.0,
            source_error_backoff_s=0.005,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(hub.run(stop))

        item = await hub.capture_once()
        stop.set()
        await task

        self.assertTrue(item.frame.frame_id.startswith("source-"))
        self.assertEqual(hub.stats().primary_frames, 0)
        self.assertGreaterEqual(hub.stats().primary_errors, 1)
        self.assertGreaterEqual(hub.stats().fallback_frames, 1)

    async def test_transient_fallback_miss_does_not_consume_heartbeat_period(self) -> None:
        primary = _Source(period_s=0.005, timeouts=True)
        fallback = _TransientFallback()
        hub = CaptureHub(
            primary=primary,
            fallback=fallback,
            frames=FrameRingBuffer(),
            fallback_after_s=0.02,
            fallback_hz=4.0,
            consumer_timeout_s=1.0,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(hub.run(stop))

        started = time.monotonic()
        item = await hub.capture_once()
        elapsed = time.monotonic() - started
        stop.set()
        await task

        self.assertTrue(item.frame.frame_id.startswith("source-"))
        self.assertEqual(fallback.attempts, 2)
        self.assertLess(elapsed, 0.15)

    async def test_fallback_copy_time_does_not_accumulate_into_heartbeat_period(self) -> None:
        primary = _Source(period_s=0.005, timeouts=True)
        fallback = _TimestampedSlowSource(period_s=0.08)
        hub = CaptureHub(
            primary=primary,
            fallback=fallback,
            frames=FrameRingBuffer(),
            fallback_after_s=0.02,
            fallback_hz=4.0,
            consumer_timeout_s=1.0,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(hub.run(stop))

        await asyncio.sleep(0.65)
        stop.set()
        await task

        stats = hub.stats()
        self.assertGreaterEqual(stats.fallback_frames, 3)
        self.assertLess(stats.max_gap_ns, 300_000_000)


if __name__ == "__main__":
    unittest.main()
