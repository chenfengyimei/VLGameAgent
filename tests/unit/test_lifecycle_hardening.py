from __future__ import annotations

import argparse
import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from apps.agent.__main__ import _has_fatal_provider_error, cli
from apps.agent.run import build_parser
from tests.integration.test_causal_receipts import writer_at
from uga.core.errors import FatalRuntimeError
from uga.recording.finalize import finalize_episode
from uga.recording.frame_queue import RecordingFailure
from uga.recording.schema import EpisodeResult
from uga.safety.focus_guard import AgentEnableState
from uga.safety.shutdown import SafetyShutdown, ShutdownCause
from uga.time.clock import ManualClock, UGATime


class PublicCliTests(unittest.TestCase):
    def test_console_and_direct_entry_have_identical_safety_options(self) -> None:
        options = [
            "--profile",
            "fixture.yaml",
            "--watchdog-timeout-seconds",
            "7",
            "--decision-timeout-seconds",
            "11",
            "--vlm-no-thinking",
        ]
        expected = vars(build_parser().parse_args(options))
        with (
            patch("sys.argv", ["uga-agent", "run", *options]),
            patch("apps.agent.__main__._run_safely", return_value=0) as run,
            self.assertRaises(SystemExit) as exit_status,
        ):
            cli()
        self.assertEqual(exit_status.exception.code, 0)
        actual = dict(vars(run.call_args[0][0]))
        actual.pop("command")
        self.assertEqual(actual, expected)
        self.assertIsInstance(run.call_args[0][0], argparse.Namespace)

    def test_nested_resource_failure_is_not_retryable(self) -> None:
        self.assertTrue(
            _has_fatal_provider_error(
                ExceptionGroup(
                    "cleanup",
                    [FatalRuntimeError("disk or device requires operator")],
                )
            )
        )


class SafetyLatchTests(unittest.TestCase):
    def test_startup_cannot_rearm_an_early_emergency(self) -> None:
        enabled = AgentEnableState(False)
        shutdown = SafetyShutdown(ManualClock(100), MagicMock(), MagicMock(), MagicMock(), enabled)
        self.assertTrue(shutdown.arm())
        shutdown.trip(ShutdownCause.EMERGENCY_HOTKEY)
        self.assertFalse(shutdown.arm())
        self.assertFalse(enabled.get())

    def test_slow_cleanup_does_not_hide_latch_or_block_status(self) -> None:
        entered, release = threading.Event(), threading.Event()
        executor = MagicMock()
        executor.release_all.side_effect = lambda: (entered.set(), release.wait(2))
        enabled = AgentEnableState(True)
        shutdown = SafetyShutdown(ManualClock(100), MagicMock(), MagicMock(), executor, enabled)
        thread = threading.Thread(target=lambda: shutdown.trip(ShutdownCause.EMERGENCY_HOTKEY))
        try:
            thread.start()
            self.assertTrue(entered.wait(1))
            start = time.monotonic()
            self.assertIsNotNone(shutdown.tripped)
            self.assertFalse(shutdown.arm())
            self.assertFalse(enabled.get())
            self.assertLess(time.monotonic() - start, 0.1)
        finally:
            release.set()
            thread.join(1)
        self.assertEqual(shutdown.tripped.cleanup_errors, ())


class BoundedPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_codec_close_cannot_publish_abandoned_episode(self) -> None:
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()

        class StuckFlush:
            def append(self, frame):  # type: ignore[no-untyped-def]
                pass

            def close(self):  # type: ignore[no-untyped-def]
                entered.set()
                release.wait(2)
                closed.set()

        with tempfile.TemporaryDirectory() as tmp:
            writer = writer_at(Path(tmp))
            writer.attach_video(StuckFlush())
            try:
                with self.assertRaises(RecordingFailure):
                    await finalize_episode(
                        writer, EpisodeResult.SUCCESS, UGATime(100), timeout_s=0.04
                    )
                self.assertTrue(entered.is_set())
                self.assertFalse((Path(tmp) / "causal-test").exists())
            finally:
                release.set()
                await asyncio.to_thread(closed.wait, 1)
                await asyncio.sleep(0.02)
            self.assertFalse((Path(tmp) / "causal-test").exists())
            self.assertTrue(writer.staging_path.exists())
            writer.abort()

    async def test_codec_flush_failure_is_fatal_and_retains_staging(self) -> None:
        class BrokenFlush:
            def append(self, frame):  # type: ignore[no-untyped-def]
                pass

            def close(self):  # type: ignore[no-untyped-def]
                raise OSError("test disk full during trailer flush")

        with tempfile.TemporaryDirectory() as tmp:
            writer = writer_at(Path(tmp))
            writer.attach_video(BrokenFlush())
            with self.assertRaises(RecordingFailure) as caught:
                await finalize_episode(writer, EpisodeResult.SUCCESS, UGATime(100))
            self.assertTrue(_has_fatal_provider_error(caught.exception))
            self.assertIsInstance(caught.exception.__cause__, OSError)
            self.assertFalse((Path(tmp) / "causal-test").exists())
            self.assertTrue(writer.staging_path.exists())
            writer.abort()
