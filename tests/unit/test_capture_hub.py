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
    async def test_primary_capture_can_be_throttled_for_lightweight_agents(self) -> None:
        source = _Source(period_s=0.001)
        hub = CaptureHub(
            primary=source,
            frames=FrameRingBuffer(),
            primary_hz=10.0,
            consumer_timeout_s=1.0,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(hub.run(stop))

        await hub.capture_once()
        await asyncio.sleep(0.22)
        stop.set()
        await task

        self.assertGreaterEqual(hub.stats().primary_frames, 2)
        self.assertLessEqual(hub.stats().primary_frames, 3)

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

        # Wait on the observable producer condition instead of assuming the
        # Windows CI scheduler will run five worker-thread captures in 120 ms.
        # The consumer remains idle throughout this bounded wait, which is the
        # behavior this test is intended to prove.
        deadline = asyncio.get_running_loop().time() + 1.0
        while True:
            latest = frames.latest()
            if latest is not None and latest.sequence >= first.sequence + 5:
                break
            if asyncio.get_running_loop().time() >= deadline:
                self.fail("capture producer did not advance while consumer was idle")
            await asyncio.sleep(0.005)
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

    async def test_gap_statistics_stay_bounded_over_long_runs(self) -> None:
        # F16 regression: the gap history was an unbounded list and stats()
        # sorted the whole thing under the capture lock.  The rolling window
        # plus O(1) accumulators keep memory and stats() cost flat no matter
        # how many frames a run accepts.
        from uga.capture import hub as hub_module

        hub = CaptureHub(
            primary=_Source(period_s=0.001),
            frames=FrameRingBuffer(),
            consumer_timeout_s=1.0,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(hub.run(stop))

        deadline = asyncio.get_running_loop().time() + 5.0
        while (
            hub.stats().accepted_frames < hub_module._GAP_WINDOW + 100
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.01)
        stats = hub.stats()
        stop.set()
        await task

        self.assertGreater(stats.accepted_frames, hub_module._GAP_WINDOW)
        window = hub._gaps_ns  # type: ignore[attr-defined]
        self.assertLessEqual(len(window), hub_module._GAP_WINDOW)
        self.assertGreater(len(window), 0)
        self.assertGreaterEqual(hub._gap_count, stats.accepted_frames - 1)  # type: ignore[attr-defined]
        # The reported max covers the WHOLE run (all-time accumulator), not
        # just the rolling window.
        self.assertEqual(stats.max_gap_ns, max(hub._gap_max_ns, 0))  # type: ignore[attr-defined]

    async def test_slow_recorder_does_not_block_latest_frame_publication(self) -> None:
        # F16: a slow recorder callback must not stall the publish path — the
        # ring buffer keeps delivering the newest frame while the recorder
        # catches up.
        release = threading.Event()

        class _SlowRecorder:
            def __init__(self) -> None:
                self.parked = False

            def record_frame(self, frame: Frame) -> None:
                if not self.parked:
                    self.parked = True
                    release.wait(timeout=2.0)

        recorder = _SlowRecorder()
        hub = CaptureHub(
            primary=_Source(period_s=0.001),
            frames=FrameRingBuffer(),
            record_frame=recorder.record_frame,
            consumer_timeout_s=1.0,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(hub.run(stop))

        # The first accepted frame parks the recorder callback; the publish
        # path must keep accepting later frames instead of stalling behind it.
        deadline = asyncio.get_running_loop().time() + 2.0
        while (
            not recorder.parked and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.005)
        self.assertTrue(recorder.parked)
        parked_stats = hub.stats()
        self.assertGreaterEqual(parked_stats.accepted_frames, 1)

        release.set()
        deadline = asyncio.get_running_loop().time() + 2.0
        while (
            hub.stats().accepted_frames <= parked_stats.accepted_frames
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.005)
        stats = hub.stats()
        stop.set()
        await task

        self.assertGreater(stats.accepted_frames, parked_stats.accepted_frames)
