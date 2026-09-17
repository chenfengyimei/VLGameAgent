from __future__ import annotations

import asyncio
import ctypes
import threading
import time
import unittest
from typing import cast
from unittest.mock import MagicMock, patch

from tests.helpers import frame
from tests.unit.test_native_capture_security import FakeLibrary, FakeWindows
from uga.capture.hub import CaptureHub
from uga.capture.native_ctypes import (
    CtypesNativeCaptureDriver,
    NativeBackendId,
    NativeCaptureLibrary,
)
from uga.capture.ring_buffer import FrameRingBuffer
from uga.core.errors import BackendStateError, BackendUnavailableError
from uga.windows.backend import WindowBackend


class DriverLifetimeTests(unittest.TestCase):
    def test_stop_cannot_destroy_a_handle_while_capture_uses_it(self) -> None:
        windows = FakeWindows()
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

        class SlowLibrary(FakeLibrary):
            def next(self, handle, timeout_ms):  # type: ignore[no-untyped-def]
                entered.set()
                release.wait(2)
                return super().next(handle, timeout_ms)

        library = SlowLibrary(windows)
        driver = CtypesNativeCaptureDriver(
            NativeBackendId.WGC, cast(NativeCaptureLibrary, library), cast(WindowBackend, windows)
        )
        driver.start(windows.identity)
        errors: list[BaseException] = []

        def capture_once() -> None:
            try:
                driver.capture()
            except BackendStateError as exc:
                errors.append(exc)

        capture = threading.Thread(target=capture_once)
        stop = threading.Thread(target=lambda: (driver.stop(), stopped.set()))
        try:
            capture.start()
            self.assertTrue(entered.wait(1))
            stop.start()
            self.assertFalse(stopped.wait(0.03))
            self.assertEqual(library.destroy_calls, 0)
        finally:
            release.set()
            capture.join(1)
            stop.join(1)
        self.assertTrue(stopped.is_set())
        self.assertEqual(len(errors), 1)
        driver.stop()
        self.assertEqual(library.destroy_calls, 1)

    def test_buffer_checked_before_pointer_copy_and_released_on_error(self) -> None:
        library = object.__new__(NativeCaptureLibrary)
        dll = MagicMock()

        def fill(handle, timeout, pointer):  # type: ignore[no-untyped-def]
            native = pointer._obj
            native.width, native.height = 0xFFFFFFFF, 0xFFFFFFFF
            native.stride_bytes, native.data_len = 8, 16
            native.data = ctypes.cast(ctypes.c_void_p(1), ctypes.POINTER(ctypes.c_uint8))
            return 0

        dll.uga_capture_next.side_effect = fill
        library._dll = dll
        with (
            patch("uga.capture.native_ctypes.ctypes.string_at") as copy,
            self.assertRaisesRegex(BackendUnavailableError, "geometry"),
        ):
            library.next(ctypes.c_void_p(1), 250)
        copy.assert_not_called()
        dll.uga_capture_frame_release.assert_called_once()

    def test_invalid_timeout_never_wraps_unsigned(self) -> None:
        for invalid in (-1, 10001, True):
            with self.subTest(invalid=invalid), self.assertRaises(BackendUnavailableError):
                CtypesNativeCaptureDriver(NativeBackendId.WGC, MagicMock(), MagicMock(), invalid)


class CaptureDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_stuck_source_terminates_without_retrying_abandoned_thread(self) -> None:
        release = threading.Event()

        class Stuck:
            calls = 0

            def capture(self):  # type: ignore[no-untyped-def]
                self.calls += 1
                release.wait(2)
                return frame(1)

        source = Stuck()
        hub = CaptureHub(primary=source, frames=FrameRingBuffer(), capture_operation_timeout_s=0.05)
        start = time.monotonic()
        try:
            with self.assertRaises(ExceptionGroup):
                await hub.run(asyncio.Event())
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertEqual(source.calls, 1)
        finally:
            release.set()

    async def test_stop_does_not_wait_for_full_capture_timeout(self) -> None:
        release = threading.Event()

        class Stuck:
            def capture(self):  # type: ignore[no-untyped-def]
                release.wait(2)
                return frame(1)

        stop = asyncio.Event()
        hub = CaptureHub(primary=Stuck(), frames=FrameRingBuffer())
        task = asyncio.create_task(hub.run(stop))
        try:
            await asyncio.sleep(0.02)
            stop.set()
            await asyncio.wait_for(task, 0.3)
            self.assertEqual(hub.stats().accepted_frames, 0)
        finally:
            release.set()
