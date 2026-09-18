"""Exact fallback scheduling contracts; no wall-clock throughput assumptions."""
from __future__ import annotations

import asyncio
import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.helpers import frame
from uga.capture import hub as hub_module
from uga.capture.hub import CaptureHub
from uga.capture.ring_buffer import FrameRingBuffer
from uga.core.errors import CaptureTimeoutError


class CaptureCadenceTests(unittest.IsolatedAsyncioTestCase):
    async def run_timeline(
        self, *, copy_seconds: float = 0.08, late_wake: float = 0.0,
        fail_first: bool = False, primary_during_first_wait: bool = False,
        quiet_seconds: float = 0.02,
    ) -> tuple[list[float], CaptureHub]:
        now = 10.0  # Nonzero so truthiness cannot silently change the schedule.
        starts: list[float] = []
        stop = asyncio.Event()
        published = 0
        waits = 0

        class Source:
            def capture(self):  # type: ignore[no-untyped-def]
                nonlocal now
                starts.append(now)
                sampled = now
                now += copy_seconds
                if fail_first and len(starts) == 1:
                    raise CaptureTimeoutError("transient readback miss")
                return frame(len(starts), timestamp_ns=round(sampled * 1e9))

        class ImmediateWorker:
            async def call(self, operation, budget):  # type: ignore[no-untyped-def]
                # Only thread execution is replaced. The real fallback loop,
                # timestamp publication, rate calculation and retry branches run.
                return operation()

        source = Source()
        hub = CaptureHub(primary=source, fallback=source, frames=FrameRingBuffer(),
                         fallback_after_s=quiet_seconds, fallback_hz=4.0)
        hub._fallback_worker = ImmediateWorker()  # type: ignore[assignment]
        publish = hub._publish

        def record(source_frame, source_name, sampled):  # type: ignore[no-untyped-def]
            nonlocal published, now
            # Include publication overhead as well as readback overhead.
            now += 0.005
            publish(source_frame, source_name, sampled)
            published += 1
            if published == 4:
                stop.set()

        async def wait_for(waiter, timeout):  # type: ignore[no-untyped-def]
            nonlocal now, waits
            waiter.close()
            waits += 1
            now += timeout + late_wake
            if primary_during_first_wait and waits == 1:
                hub._last_frame_monotonic = now
            raise TimeoutError

        async def sleep(seconds):  # type: ignore[no-untyped-def]
            nonlocal now
            now += seconds
            await asyncio.sleep(0)

        def monotonic() -> float:
            nonlocal now
            # Real monotonic time advances across boundary rechecks; move only
            # one ULP so floating-point equality cannot trap the virtual loop.
            now = math.nextafter(now, math.inf)
            return now

        with (
            patch.object(hub_module, "time", SimpleNamespace(monotonic=monotonic)),
            patch.object(hub_module, "asyncio", SimpleNamespace(wait_for=wait_for, sleep=sleep)),
            patch.object(hub, "_publish", side_effect=record),
        ):
            await hub._capture_fallback(stop)
        return starts, hub

    async def test_copy_and_publication_do_not_extend_sample_period(self) -> None:
        starts, hub = await self.run_timeline()
        for actual, expected in zip(starts, (10.02, 10.27, 10.52, 10.77), strict=True):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(hub.stats().fallback_frames, 4)
        self.assertEqual(hub.stats().max_gap_ns, 250_000_000)

    async def test_slow_copy_does_not_add_another_full_period_or_catch_up(self) -> None:
        starts, hub = await self.run_timeline(copy_seconds=0.4)
        for actual, expected in zip(starts, (10.02, 10.425, 10.83, 11.235), strict=True):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(hub.stats().max_gap_ns, 405_000_000)

    async def test_late_scheduler_wakeup_never_creates_a_catchup_burst(self) -> None:
        starts, _ = await self.run_timeline(late_wake=0.3)
        for left, right in zip(starts, starts[1:], strict=False):
            self.assertAlmostEqual(right - left, 0.55)

    async def test_transient_failed_capture_does_not_consume_output_rate_budget(self) -> None:
        starts, hub = await self.run_timeline(fail_first=True)
        self.assertEqual(len(starts), 5)
        self.assertEqual(hub.stats().fallback_errors, 1)
        self.assertAlmostEqual(starts[1] - starts[0], 0.105)
        for left, right in zip(starts[1:], starts[2:], strict=False):
            self.assertAlmostEqual(right - left, 0.25)

    async def test_primary_publication_during_wait_defers_fallback(self) -> None:
        starts, _ = await self.run_timeline(primary_during_first_wait=True)
        self.assertAlmostEqual(starts[0], 10.04)

    async def test_quiet_threshold_uses_sample_time_not_copy_completion(self) -> None:
        starts, hub = await self.run_timeline(quiet_seconds=0.25)
        for actual, expected in zip(starts, (10.25, 10.5, 10.75, 11.0), strict=True):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(hub.stats().max_gap_ns, 250_000_000)
