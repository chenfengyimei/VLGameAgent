from __future__ import annotations

import ctypes
import threading
import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

from tests.unit.test_native_capture_security import FakeLibrary, FakeWindows
from uga.capture.native_ctypes import (
    CtypesNativeCaptureDriver,
    NativeBackendId,
    NativeCaptureLibrary,
)
from uga.core.errors import BackendStateError, BackendUnavailableError
from uga.windows.backend import WindowBackend


class NativeLifecycleBoundsTests(unittest.TestCase):
    def test_stop_never_destroys_handle_while_next_is_using_it(self) -> None:
        windows = FakeWindows()
        entered, release = threading.Event(), threading.Event()
        class BlockingLibrary(FakeLibrary):
            def next(self, handle, timeout_ms):  # type: ignore[no-untyped-def]
                entered.set()
                assert release.wait(2)
                return super().next(handle, timeout_ms)
        library = BlockingLibrary(windows)
        driver = CtypesNativeCaptureDriver(
            NativeBackendId.WGC, cast(NativeCaptureLibrary, library), cast(WindowBackend, windows)
        )
        driver.start(windows.identity)
        errors = []
        def capture() -> None:
            try:
                driver.capture()
            except BackendStateError as exc:
                errors.append(exc)
        reader = threading.Thread(target=capture)
        reader.start()
        self.assertTrue(entered.wait(1))
        closer = threading.Thread(target=driver.stop)
        closer.start()
        self.assertTrue(driver._stopping.wait(1))
        self.assertEqual(library.destroy_calls, 0)
        release.set()
        reader.join(2)
        closer.join(2)
        self.assertFalse(reader.is_alive() or closer.is_alive())
        self.assertEqual(library.destroy_calls, 1)
        self.assertEqual(len(errors), 1)
        driver.stop()
        self.assertEqual(library.destroy_calls, 1)

    def test_bad_buffer_dimensions_are_rejected_before_memory_copy(self) -> None:
        library = NativeCaptureLibrary.__new__(NativeCaptureLibrary)
        def fill(handle, timeout, output):  # type: ignore[no-untyped-def]
            native = output._obj
            native.width = native.height = 2
            native.stride_bytes = 8
            native.data_len = 1 << 40
            native.data = ctypes.cast(ctypes.c_void_p(1), ctypes.POINTER(ctypes.c_uint8))
            return 0
        release = Mock()
        library._dll = SimpleNamespace(uga_capture_next=fill, uga_capture_frame_release=release)
        with (
            patch("uga.capture.native_ctypes.ctypes.string_at") as copy,
            self.assertRaisesRegex(BackendUnavailableError, "bounded buffer"),
        ):
            library.next(ctypes.c_void_p(1), 250)
        copy.assert_not_called()
        release.assert_called_once()

    def test_timeout_overflow_and_boolean_are_not_wrapped_into_native_unsigned(self) -> None:
        windows = FakeWindows()
        for value in (-1, True, 1001, 1 << 33):
            with self.subTest(value=value), self.assertRaises(BackendUnavailableError):
                CtypesNativeCaptureDriver(
                    NativeBackendId.WGC, cast(NativeCaptureLibrary, FakeLibrary(windows)),
                    cast(WindowBackend, windows), value,
                )
