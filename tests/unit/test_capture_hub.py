from __future__ import annotations

import asyncio
import threading
import time
import unittest
from dataclasses import replace

from tests.helpers import frame
from uga.capture.hub import CaptureHub
from uga.capture.ring_buffer import FrameRingBuffer
from uga.core.errors import CaptureTimeoutError
from uga.time.clock import UGATime


class _Source:
    def __init__(self, *, period_s: float = 0.01, timeouts: bool = False) -> None:
        self.period_s = period_s
        self.timeouts = timeouts
        self.count = 0
        self._lock = threading.Lock()

    def capture(self):  # type: ignore[no-untyped-def]
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


if __name__ == "__main__":
    unittest.main()
