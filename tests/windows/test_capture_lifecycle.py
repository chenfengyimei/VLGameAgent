from __future__ import annotations

import os
import unittest

from tests.windows.window_fixture import (
    capture_test_window,
    distinct_monitor_position,
    minimize_window,
    move_window,
    resize_window,
    restore_window,
)
from uga.capture.fallback import GDIFallbackCaptureBackend
from uga.core.errors import BackendUnavailableError
from uga.windows.backend import Win32WindowBackend


@unittest.skipUnless(os.name == "nt", "Windows-only contract")
class GdiCaptureLifecycleTests(unittest.TestCase):
    """Capture must follow live window state without ever returning stale data."""

    def setUp(self) -> None:
        self.windows = Win32WindowBackend()

    def test_capture_tracks_window_resize(self) -> None:
        with capture_test_window(self.windows) as (hwnd, target):
            backend = GDIFallbackCaptureBackend(self.windows)
            backend.start(target)
            try:
                first = backend.capture()
                self.assertGreater(first.width, 0)
                resize_window(hwnd, 640, 480)
                snapshot = self.windows.snapshot(hwnd)
                second = backend.capture()
                self.assertEqual(
                    (second.width, second.height),
                    (
                        int(snapshot.client_screen_rect.width),
                        int(snapshot.client_screen_rect.height),
                    ),
                )
                self.assertGreater(second.width, first.width)
                self.assertEqual(second.window_identity, target)
            finally:
                backend.stop()

    def test_capture_fails_closed_while_minimized(self) -> None:
        with capture_test_window(self.windows) as (hwnd, target):
            backend = GDIFallbackCaptureBackend(self.windows)
            backend.start(target)
            try:
                first = backend.capture()
                self.assertGreater(first.width, 0)
                minimize_window(hwnd)
                self.assertFalse(backend.probe(target).available)
                with self.assertRaisesRegex(BackendUnavailableError, "minimized"):
                    backend.capture()
                restore_window(hwnd)
                second = backend.capture()
                self.assertEqual(second.window_identity, target)
                self.assertGreater(second.width, 0)
            finally:
                backend.stop()

    def test_capture_follows_window_across_monitors(self) -> None:
        with capture_test_window(self.windows) as (hwnd, target):
            position = distinct_monitor_position(hwnd)
            if position is None:
                self.skipTest("requires a second monitor")
            backend = GDIFallbackCaptureBackend(self.windows)
            backend.start(target)
            try:
                first = backend.capture()
                move_window(hwnd, position[0], position[1])
                second = backend.capture()
                self.assertEqual(second.window_identity, target)
                self.assertEqual(
                    (second.width, second.height), (first.width, first.height)
                )
            finally:
                backend.stop()


if __name__ == "__main__":
    unittest.main()
