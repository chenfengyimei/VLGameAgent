from __future__ import annotations

from uga.capture.base import (
    CaptureBackend,
    CaptureCapability,
    CaptureProbe,
    GraphicsAPI,
    WindowMode,
)
from uga.capture.frame import Frame, PixelFormat
from uga.capture.native_adapter import NativeCaptureDriver
from uga.capture.native_ctypes import NativeBackendId, load_default_driver
from uga.core.errors import BackendUnavailableError
from uga.windows.window_identity import WindowIdentity

_CAPABILITY = CaptureCapability(
    window_modes=frozenset(
        {WindowMode.WINDOWED, WindowMode.BORDERLESS, WindowMode.EXCLUSIVE_FULLSCREEN}
    ),
    graphics_apis=frozenset(
        {GraphicsAPI.DX11, GraphicsAPI.DX12, GraphicsAPI.VULKAN, GraphicsAPI.OPENGL}
    ),
    pixel_formats=frozenset({PixelFormat.BGRA8}),
    supports_multi_monitor=True,
    supports_hdr=False,
    supports_resize=True,
    supports_minimized=False,
)


class DXGIDuplicationBackend(CaptureBackend):
    backend_id = "dxgi_duplication"

    def __init__(self, driver: NativeCaptureDriver | None = None) -> None:
        super().__init__()
        self._driver = driver if driver is not None else load_default_driver(NativeBackendId.DXGI)

    def probe(self, target: WindowIdentity) -> CaptureProbe:
        if self._driver is None:
            return CaptureProbe(False, 0, "native DXGI provider is not installed", _CAPABILITY)
        available, reason, capability = self._driver.probe(target)
        return CaptureProbe(available, 90 if available else 0, reason, capability)

    def _start(self, target: WindowIdentity) -> None:
        if self._driver is None:
            raise BackendUnavailableError("native DXGI provider is not installed")
        self._driver.start(target)

    def _capture(self) -> Frame:
        if self._driver is None or self._target is None:
            raise BackendUnavailableError("native DXGI provider is not installed")
        item = self._driver.capture()
        return Frame(
            item.frame_id,
            item.capture_timestamp,
            item.present_estimate,
            self._target,
            item.width,
            item.height,
            item.stride_bytes,
            item.pixel_format,
            item.physical_rect,
            item.client_rect,
            self.backend_id,
            item.buffer_handle,
        )

    def _stop(self) -> None:
        if self._driver is not None:
            self._driver.stop()
