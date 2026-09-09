from __future__ import annotations

import ctypes
import hashlib
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

from tests.helpers import identity
from uga.capture.frame import BufferHandle, BufferKind, PixelFormat
from uga.capture.native_adapter import NativeCapturedFrame
from uga.capture.native_ctypes import (
    _EXPECTED_ABI,
    CtypesNativeCaptureDriver,
    NativeBackendId,
    NativeCaptureLibrary,
    load_default_driver,
)
from uga.core.errors import BackendUnavailableError, CaptureAccessLostError
from uga.time.clock import UGATime
from uga.windows.backend import WindowBackend
from uga.windows.coordinates import Rect


class FakeWindows:
    def __init__(self) -> None:
        self.identity = identity()

    def snapshot(self, hwnd: int) -> SimpleNamespace:
        if hwnd != self.identity.hwnd:
            raise RuntimeError("unknown window")
        return SimpleNamespace(identity=self.identity)


class FakeLibrary:
    def __init__(self, windows: FakeWindows, *, mutate_during_next: bool = False) -> None:
        self.windows = windows
        self.mutate_during_next = mutate_during_next
        self.next_calls = 0
        self.destroy_calls = 0

    def create(self, backend: NativeBackendId, hwnd: int) -> ctypes.c_void_p:
        del backend, hwnd
        return ctypes.c_void_p(1)

    def next(self, handle: ctypes.c_void_p, timeout_ms: int) -> NativeCapturedFrame:
        del handle, timeout_ms
        self.next_calls += 1
        if self.mutate_during_next:
            self.windows.identity = replace(
                self.windows.identity, pid=self.windows.identity.pid + 1
            )
        payload = bytes(16)
        return NativeCapturedFrame(
            "frame",
            UGATime(10),
            None,
            2,
            2,
            8,
            PixelFormat.BGRA8,
            Rect(0, 0, 2, 2),
            Rect(0, 0, 2, 2),
            BufferHandle("buffer", BufferKind.CPU_BYTES, len(payload), payload),
        )

    def destroy(self, handle: ctypes.c_void_p) -> None:
        del handle
        self.destroy_calls += 1


class NativeCaptureSecurityTests(unittest.TestCase):
    def test_digest_mismatch_prevents_library_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "uga_capture.dll"
            path.write_bytes(b"not a dll")
            with (
                patch("uga.capture.native_ctypes.ctypes.CDLL") as loader,
                self.assertRaisesRegex(BackendUnavailableError, "SHA-256 mismatch"),
            ):
                NativeCaptureLibrary(path, expected_sha256="0" * 64)
            loader.assert_not_called()

    def test_library_loads_a_verified_private_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "uga_capture.dll"
            payload = b"native capture module bytes"
            source.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            with patch("uga.capture.native_ctypes.ctypes.CDLL") as loader:
                loader.return_value.uga_capture_abi_version = MagicMock(
                    return_value=_EXPECTED_ABI
                )
                library = NativeCaptureLibrary(source, expected_sha256=digest)
            loader.assert_called_once()
            loaded = Path(str(loader.call_args[0][0]))
            self.assertNotEqual(loaded, source)
            self.assertEqual(hashlib.sha256(loaded.read_bytes()).hexdigest(), digest)
            self.assertEqual(library.path, loaded)

    def test_explicit_environment_request_fails_loud_when_library_missing(self) -> None:
        env = {"UGA_NATIVE_CAPTURE_DLL": r"Z:\missing\uga_capture.dll"}
        with (
            patch.dict(os.environ, env),
            patch("uga.capture.native_ctypes.find_native_library", return_value=None),
        ):
            with self.assertRaisesRegex(
                BackendUnavailableError, "explicit native capture library is missing"
            ):
                load_default_driver(NativeBackendId.WGC, cast(WindowBackend, FakeWindows()))
            # An unwired probe request stays graceful even when pinned.
            self.assertIsNone(load_default_driver(NativeBackendId.WGC, None))
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(
                load_default_driver(NativeBackendId.WGC, cast(WindowBackend, FakeWindows()))
            )

    def test_explicit_environment_request_fails_loud_on_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "uga_capture.dll"
            source.write_bytes(b"tampered module bytes")
            env = {
                "UGA_NATIVE_CAPTURE_DLL": str(source),
                "UGA_NATIVE_CAPTURE_SHA256": "0" * 64,
            }
            with (
                patch.dict(os.environ, env),
                patch("uga.capture.native_ctypes.ctypes.CDLL") as loader,
                self.assertRaisesRegex(BackendUnavailableError, "SHA-256 mismatch"),
            ):
                load_default_driver(NativeBackendId.WGC, cast(WindowBackend, FakeWindows()))
            loader.assert_not_called()

    def test_identity_change_before_capture_destroys_session(self) -> None:
        windows = FakeWindows()
        library = FakeLibrary(windows)
        driver = CtypesNativeCaptureDriver(
            NativeBackendId.WGC,
            cast(NativeCaptureLibrary, library),
            cast(WindowBackend, windows),
        )
        target = windows.identity
        driver.start(target)
        windows.identity = replace(target, pid=target.pid + 1)

        with self.assertRaisesRegex(CaptureAccessLostError, "identity changed"):
            driver.capture()

        self.assertEqual(library.next_calls, 0)
        self.assertEqual(library.destroy_calls, 1)

    def test_identity_change_during_capture_discards_frame(self) -> None:
        windows = FakeWindows()
        library = FakeLibrary(windows, mutate_during_next=True)
        driver = CtypesNativeCaptureDriver(
            NativeBackendId.DXGI,
            cast(NativeCaptureLibrary, library),
            cast(WindowBackend, windows),
        )
        driver.start(windows.identity)

        with self.assertRaisesRegex(CaptureAccessLostError, "identity changed"):
            driver.capture()

        self.assertEqual(library.next_calls, 1)
        self.assertEqual(library.destroy_calls, 1)


if __name__ == "__main__":
    unittest.main()
