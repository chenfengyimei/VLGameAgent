from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

import av

from tests.helpers import frame
from tests.integration.test_causal_receipts import writer_at
from tests.unit.test_capture_hub import _Source
from tests.unit.test_video_recorder import _frame
from uga.capture.hub import CaptureHub
from uga.capture.ring_buffer import FrameRingBuffer
from uga.recording.channel import RecorderChannel
from uga.recording.video import PyAvVideoRecorder
from uga.time.clock import UGATime


class ChannelTests(unittest.TestCase):
    def test_accounting_remains_exact_under_fast_completions(self) -> None:
        channel = RecorderChannel(capacity=2, max_pending_bytes=10000)
        for _ in range(500):
            channel.submit(lambda: None, weight_bytes=3)
        channel.close()
        self.assertEqual(channel.stats()["pending_bytes"], 0)
        self.assertEqual(channel.stats()["pending_entries"], 0)
        self.assertEqual(channel.stats()["completed"], 500)
        self.assertTrue(channel.drained)

    def test_byte_reservations_include_running_work(self) -> None:
        release = threading.Event()
        channel = RecorderChannel(capacity=8, max_pending_bytes=10)
        channel.submit(lambda: release.wait(2), weight_bytes=7)
        try:
            with self.assertRaises(TimeoutError):
                channel.submit(lambda: None, timeout_s=0, weight_bytes=7)
            self.assertEqual(channel.stats()["pending_bytes"], 7)
        finally:
            release.set()
            channel.close()

    def test_failed_operation_is_not_counted_as_completed(self) -> None:
        def fail() -> None:
            raise OSError("simulated disk full")
        channel = RecorderChannel()
        channel.submit(fail, weight_bytes=10)
        with self.assertRaises(RuntimeError):
            channel.close()
        self.assertEqual(channel.stats()["completed"], 0)
        self.assertEqual(channel.stats()["discarded"], 1)
        self.assertFalse(channel.drained)

    def test_close_does_not_wait_forever_for_codec(self) -> None:
        release = threading.Event()
        started = threading.Event()
        def blocked() -> None:
            started.set()
            release.wait(2)
        channel = RecorderChannel(capacity=1)
        channel.submit(blocked)
        self.assertTrue(started.wait(1))
        try:
            begin = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                channel.close(timeout_s=0.02)
            self.assertLess(time.monotonic() - begin, 0.5)
            with self.assertRaises(RuntimeError):
                channel.submit(lambda: None)
        finally:
            release.set()
            channel._thread.join(1)

    def test_slow_video_does_not_own_episode_metadata_lock(self) -> None:
        release = threading.Event()
        started = threading.Event()
        class SlowVideo:
            def append(self, item: object) -> None:
                started.set()
                release.wait(2)
            def close(self) -> None:
                pass
        with tempfile.TemporaryDirectory() as temporary:
            writer = writer_at(Path(temporary))
            writer.attach_video(SlowVideo())
            worker = threading.Thread(target=writer.record_frame, args=(frame(1),))
            worker.start()
            self.assertTrue(started.wait(1))
            try:
                done = threading.Event()
                def record() -> None:
                    writer.record_observation("independent", UGATime(100), {})
                    done.set()
                metadata_thread = threading.Thread(target=record)
                metadata_thread.start()
                self.assertTrue(done.wait(0.5), "video encoder blocked control-plane recording")
                metadata_thread.join(1)
            finally:
                release.set()
                worker.join(1)
                writer.abort()

    def test_video_timestamps_do_not_stretch_high_rate_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "video.mp4"
            writer = PyAvVideoRecorder(path, fps=15)
            for index in range(20):
                writer.append(
                    replace(_frame(16, 16), capture_timestamp=UGATime(index * 10_000_000))
                )
            writer.close()
            with av.open(str(path)) as recording:
                times = [float(item.time) for item in recording.decode(video=0)]
            self.assertEqual(len(times), 20)
            self.assertAlmostEqual(times[-1] - times[0], 0.19, delta=0.003)


class AsyncRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_advances_before_slow_recorder_is_released(self) -> None:
        release = threading.Event()
        started = threading.Event()
        recorded: list[str] = []
        def record(item: object) -> None:
            started.set()
            release.wait(2)
            recorded.append(item.frame_id)  # type: ignore[attr-defined]
        frames = FrameRingBuffer(capacity=64)
        hub = CaptureHub(
            primary=_Source(period_s=0.005), frames=frames, record_frame=record,
            recorder_capacity=64,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(hub.run(stop))
        try:
            await hub.capture_once()
            deadline = time.monotonic() + 1
            while hub.stats().accepted_frames < 5 and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
            self.assertTrue(started.is_set())
            self.assertGreaterEqual(hub.stats().accepted_frames, 5)
            self.assertFalse(release.is_set())
        finally:
            stop.set()
            release.set()
            await task
        self.assertTrue(hub.recording_complete)
        self.assertEqual(recorded, [item.frame.frame_id for item in frames.snapshot()])

    async def test_queue_saturation_fails_recording_instead_of_dropping(self) -> None:
        release = threading.Event()
        hub = CaptureHub(
            primary=_Source(period_s=0.005), frames=FrameRingBuffer(),
            record_frame=lambda _: release.wait(2), recorder_capacity=1,
            recorder_close_timeout_s=0.02,
        )
        try:
            with self.assertRaises((ExceptionGroup, RuntimeError)):
                await hub.run(asyncio.Event())
            self.assertFalse(hub.recording_complete)
            self.assertEqual(hub.recording_stats()["rejected"], 1)
        finally:
            release.set()
