from __future__ import annotations

import unittest

from tests.helpers import identity
from uga.capture.base import CaptureCapability, GraphicsAPI, WindowMode
from uga.capture.dxgi import DXGIDuplicationBackend
from uga.capture.frame import BufferHandle, BufferKind, PixelFormat
from uga.capture.native_adapter import NativeCapturedFrame
from uga.capture.windows_graphics_capture import WindowsGraphicsCaptureBackend
from uga.time.clock import UGATime
from uga.windows.coordinates import Rect


class FakeNativeDriver:
    def __init__(self) -> None:
        self.started = False

    def probe(self, target):  # type: ignore[no-untyped-def]
        capability = CaptureCapability(
            frozenset({WindowMode.WINDOWED}),
            frozenset({GraphicsAPI.DX11}),
            frozenset({PixelFormat.BGRA8}),
            True,
            False,
            True,
            False,
        )
        return True, "test driver", capability

    def start(self, target):  # type: ignore[no-untyped-def]
        self.started = True

    def capture(self) -> NativeCapturedFrame:
        payload = bytes(16)
        return NativeCapturedFrame(
            "native-1",
            UGATime(10),
            UGATime(9),
            2,
            2,
            8,
            PixelFormat.BGRA8,
            Rect(0, 0, 2, 2),
            Rect(0, 0, 2, 2),
            BufferHandle("native-buffer", BufferKind.CPU_BYTES, len(payload), payload),
        )

    def stop(self) -> None:
        self.started = False


class NativeAdapterTests(unittest.TestCase):
    def test_missing_native_providers_are_reported_honestly(self) -> None:
        target = identity()
        self.assertFalse(WindowsGraphicsCaptureBackend().probe(target).available)
        self.assertFalse(DXGIDuplicationBackend().probe(target).available)

    def test_wgc_adapter_preserves_native_frame_contract(self) -> None:
        target = identity()
        backend = WindowsGraphicsCaptureBackend(FakeNativeDriver())
        backend.start(target)
        captured = backend.capture()
        self.assertEqual(captured.window_identity, target)
        self.assertEqual(captured.source_backend, "windows_graphics_capture")
        backend.stop()

    def test_dxgi_adapter_preserves_native_frame_contract(self) -> None:
        target = identity()
        backend = DXGIDuplicationBackend(FakeNativeDriver())
        backend.start(target)
        captured = backend.capture()
        self.assertEqual(captured.window_identity, target)
        self.assertEqual(captured.source_backend, "dxgi_duplication")
        backend.stop()


if __name__ == "__main__":
    unittest.main()
