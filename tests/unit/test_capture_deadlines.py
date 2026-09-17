from __future__ import annotations

import asyncio
import threading
import unittest
from dataclasses import replace

from tests.helpers import frame
from uga.capture.hub import CaptureHub
from uga.capture.ring_buffer import FrameRingBuffer
from uga.core.errors import CaptureTimeoutError, ContractViolation


class CaptureDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_returns_without_joining_blocked_capture(self) -> None:
        entered, release = threading.Event(), threading.Event()

        class Stalled:
            def capture(self):  # type: ignore[no-untyped-def]
                entered.set()
                release.wait(5)
                return frame(1)

        hub = CaptureHub(primary=Stalled(), frames=FrameRingBuffer())
        stop = asyncio.Event()
        task = asyncio.create_task(hub.run(stop))
        try:
            while not entered.is_set():
                await asyncio.sleep(0.001)
            stop.set()
            await asyncio.wait_for(task, 0.5)
            self.assertEqual(hub.stats().accepted_frames, 0)
        finally:
            release.set()

    async def test_one_stalled_primary_is_not_recreated_and_fallback_survives(self) -> None:
        release = threading.Event()
        attempts = []

        class Stalled:
            def capture(self):  # type: ignore[no-untyped-def]
                attempts.append(1)
                release.wait(5)
                return frame(1)

        class Fresh:
            def capture(self):  # type: ignore[no-untyped-def]
                return frame(2)

        hub = CaptureHub(
            primary=Stalled(),
            fallback=Fresh(),
            frames=FrameRingBuffer(),
            capture_call_timeout_s=0.02,
            fallback_after_s=0.04,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(hub.run(stop))
        try:
            item = await asyncio.wait_for(hub.capture_once(), 1)
            self.assertEqual(item.frame.frame_id, "frame-2")
            self.assertEqual(len(attempts), 1)
            self.assertEqual(hub.stats().primary_errors, 1)
        finally:
            stop.set()
            await task
            release.set()

    async def test_unstarted_consumer_wait_has_a_deadline(self) -> None:
        hub = CaptureHub(primary=None, frames=FrameRingBuffer(), consumer_timeout_s=0.01)
        with self.assertRaises(CaptureTimeoutError):
            await asyncio.wait_for(hub.capture_once(), 0.5)

    def test_immutable_buffer_size_must_match_recording_byte_accounting(self) -> None:
        value = frame(1)
        value = replace(
            value,
            buffer_handle=replace(
                value.buffer_handle, payload=bytes(value.buffer_handle.readonly_view()) + b"x"
            ),
        )
        hub = CaptureHub(primary=None, frames=FrameRingBuffer(), record_frame=lambda f: None)
        with self.assertRaises(ContractViolation):
            hub._publish(value, "primary", 0.0)
        self.assertFalse(hub.recording_complete)
        self.assertEqual(hub.stats().accepted_frames, 0)
