from __future__ import annotations

import os
import unittest

from tests.windows.window_fixture import capture_test_window
from uga.capture.native_ctypes import (
    CtypesNativeCaptureDriver,
    NativeBackendId,
    NativeCaptureLibrary,
    find_native_library,
)
from uga.core.errors import BackendUnavailableError


@unittest.skipUnless(os.name == "nt", "Windows-only contract")
class NativeCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = find_native_library()
        if path is None:
            raise unittest.SkipTest("native capture DLL has not been built")
        cls.library = NativeCaptureLibrary(path)

    def test_ffi_rejects_null_hwnd_without_crossing_panic_boundary(self) -> None:
        with self.assertRaisesRegex(BackendUnavailableError, "HWND cannot be zero"):
            self.library.create(NativeBackendId.WGC, 0)

    def test_wgc_captures_owned_fixture_when_desktop_supports_it(self) -> None:
        with capture_test_window() as (_, target):
            driver = CtypesNativeCaptureDriver(NativeBackendId.WGC, self.library, timeout_ms=2000)
            try:
                driver.start(target)
                frame = driver.capture()
            except BackendUnavailableError as error:
                self.skipTest(f"WGC unavailable on this desktop: {error}")
            finally:
                driver.stop()
        self.assertGreater(frame.width, 0)
        self.assertEqual(frame.buffer_handle.size_bytes, frame.width * frame.height * 4)

    def test_dxgi_captures_owned_fixture_when_desktop_supports_it(self) -> None:
        with capture_test_window() as (_, target):
            driver = CtypesNativeCaptureDriver(NativeBackendId.DXGI, self.library, timeout_ms=2000)
            try:
                driver.start(target)
                frame = driver.capture()
            except BackendUnavailableError as error:
                self.skipTest(f"DXGI unavailable on this desktop: {error}")
            finally:
                driver.stop()
        self.assertGreater(frame.width, 0)
        self.assertEqual(frame.buffer_handle.size_bytes, frame.width * frame.height * 4)

    def test_wgc_teardown_does_not_poison_following_dxgi_create(self) -> None:
        with capture_test_window() as (_, target):
            try:
                wgc = self.library.create(NativeBackendId.WGC, target.hwnd)
            except BackendUnavailableError as error:
                self.skipTest(f"WGC unavailable on this desktop: {error}")
            self.library.destroy(wgc)

            try:
                dxgi = self.library.create(NativeBackendId.DXGI, target.hwnd)
            except BackendUnavailableError as error:
                self.skipTest(f"DXGI unavailable on this desktop: {error}")
            self.library.destroy(dxgi)


if __name__ == "__main__":
    unittest.main()
