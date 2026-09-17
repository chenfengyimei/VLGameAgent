from __future__ import annotations

import tempfile
import threading
import time
import unittest

from tests.helpers import frame
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.frame_queue import FrameRecorderQueue, RecordingFailure
from uga.recording.schema import EpisodeMetadata, EpisodeResult
from uga.time.clock import UGATime


class FrameQueueTests(unittest.TestCase):
    def test_ordered_drain_and_accounting(self) -> None:
        seen: list[str] = []
        q = FrameRecorderQueue(lambda f: seen.append(f.frame_id))
        for i in range(10):
            q.submit(frame(i + 1))
        q.close()
        self.assertEqual(seen, [frame(i + 1).frame_id for i in range(10)])
        self.assertTrue(q.complete)
        self.assertEqual(q.stats().pending_bytes, 0)

    def test_inflight_counts_towards_capacity_and_overflow_is_failure(self) -> None:
        parked, release = threading.Event(), threading.Event()

        def record(f):  # type: ignore[no-untyped-def]
            parked.set()
            release.wait(2)

        q = FrameRecorderQueue(record, max_frames=1)
        try:
            q.submit(frame(1))
            self.assertTrue(parked.wait(1))
            with self.assertRaises(RecordingFailure):
                q.submit(frame(2))
            self.assertFalse(q.complete)
        finally:
            release.set()
            with self.assertRaises(RecordingFailure):
                q.close()

    def test_byte_budget_and_encoder_failure(self) -> None:
        q = FrameRecorderQueue(lambda f: None, max_bytes=1)
        with self.assertRaises(RecordingFailure):
            q.submit(frame(1))

        def broken(f):  # type: ignore[no-untyped-def]
            raise OSError("disk full")

        q = FrameRecorderQueue(broken)
        q.submit(frame(1))
        with self.assertRaises(RecordingFailure):
            q.close()
        self.assertFalse(q.complete)

    def test_stuck_encoder_has_bounded_close_and_no_further_callbacks(self) -> None:
        parked, release = threading.Event(), threading.Event()
        seen: list[str] = []

        def record(f):  # type: ignore[no-untyped-def]
            seen.append(f.frame_id)
            parked.set()
            release.wait(2)

        q = FrameRecorderQueue(record)
        try:
            q.submit(frame(1))
            self.assertTrue(parked.wait(1))
            q.submit(frame(2))
            start = time.monotonic()
            with self.assertRaises(RecordingFailure):
                q.close(0.03)
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertFalse(q.complete)
        finally:
            release.set()
            if q._worker is not None:
                q._worker.join(1)
        self.assertEqual(seen, [frame(1).frame_id])

    def test_encoder_does_not_hold_episode_metadata_lock(self) -> None:
        parked, release, recorded = threading.Event(), threading.Event(), threading.Event()

        class SlowVideo:
            def append(self, f):  # type: ignore[no-untyped-def]
                parked.set()
                release.wait(2)

            def close(self):  # type: ignore[no-untyped-def]
                pass

        with tempfile.TemporaryDirectory() as tmp:
            writer = EpisodeWriter(
                tmp,
                EpisodeMetadata(
                    episode_id="nonblocking",
                    game_id="fixture-game",
                    game_version="1",
                    window_size=(2, 2),
                    capture_backend="test",
                    start_monotonic_ns=0,
                    task="test",
                    result=EpisodeResult.IN_PROGRESS,
                    agent_version="test",
                    policy_version="test",
                    human_controlled=False,
                ),
                require_video=False,
            )
            writer.attach_video(SlowVideo())
            q = FrameRecorderQueue(writer.record_frame)

            def metadata():  # type: ignore[no-untyped-def]
                writer.record_observation("obs", UGATime(100), {"features": [0]})
                recorded.set()

            t = threading.Thread(target=metadata, daemon=True)
            try:
                q.submit(frame(1))
                self.assertTrue(parked.wait(1))
                t.start()
                self.assertTrue(recorded.wait(0.5), "encoder held metadata lock")
            finally:
                release.set()
                t.join(1)
                q.close()
                writer.abort()
