from __future__ import annotations

import ctypes
import os
import uuid

from uga.capture.base import (
    CaptureBackend,
    CaptureCapability,
    CaptureProbe,
    GraphicsAPI,
    WindowMode,
)
from uga.capture.frame import BufferHandle, BufferKind, Frame, PixelFormat
from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.time.clock import ClockBackend, PerfCounterClock
from uga.windows.backend import WindowBackend
from uga.windows.coordinates import Rect
from uga.windows.window_identity import WindowIdentity

_CAPABILITY = CaptureCapability(
    window_modes=frozenset({WindowMode.WINDOWED}),
    graphics_apis=frozenset({GraphicsAPI.UNKNOWN}),
    pixel_formats=frozenset({PixelFormat.BGRA8}),
    supports_multi_monitor=True,
    supports_hdr=False,
    supports_resize=True,
    supports_minimized=False,
)


if os.name == "nt":
    from ctypes import wintypes

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class _RGBQUAD(ctypes.Structure):
        _fields_ = [
            ("rgbBlue", ctypes.c_ubyte),
            ("rgbGreen", ctypes.c_ubyte),
            ("rgbRed", ctypes.c_ubyte),
            ("rgbReserved", ctypes.c_ubyte),
        ]

    class _BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", _RGBQUAD * 1)]


class GDIFallbackCaptureBackend(CaptureBackend):
    """Conservative CPU fallback for the same visible bounds as native capture."""

    backend_id = "gdi_fallback"
    _BI_RGB = 0
    _DIB_RGB_COLORS = 0
    _SRCCOPY = 0x00CC0020
    _CAPTUREBLT = 0x40000000

    def __init__(self, windows: WindowBackend, clock: ClockBackend | None = None) -> None:
        super().__init__()
        self._windows = windows
        self._clock = clock or PerfCounterClock()
        if os.name == "nt":
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
            self._configure_signatures()

    def probe(self, target: WindowIdentity) -> CaptureProbe:
        if os.name != "nt":
            return CaptureProbe(False, 0, "GDI capture requires Windows", _CAPABILITY)
        if self._user32.IsIconic(ctypes.c_void_p(target.hwnd)):
            # IsWindowVisible stays true for minimized windows; GDI cannot
            # capture an iconic client area, so the probe must say so.
            return CaptureProbe(False, 0, "target is minimized", _CAPABILITY)
        try:
            snapshot = self._windows.snapshot(target.hwnd)
        except Exception as error:
            return CaptureProbe(False, 0, f"target unavailable: {error}", _CAPABILITY)
        if snapshot.identity != target:
            return CaptureProbe(False, 0, "target window generation changed", _CAPABILITY)
        if not snapshot.is_visible:
            return CaptureProbe(False, 0, "target is not visible", _CAPABILITY)
        return CaptureProbe(
            True, 20, "visible windowed target; compatibility fallback", _CAPABILITY
        )

    def _start(self, target: WindowIdentity) -> None:
        probe = self.probe(target)
        if not probe.available:
            raise BackendUnavailableError(probe.reason)

    def _capture(self) -> Frame:
        if os.name != "nt" or self._target is None:
            raise BackendUnavailableError("GDI capture is not started on Windows")
        if self._user32.IsIconic(ctypes.c_void_p(self._target.hwnd)):
            # The snapshot layer rejects iconic client rects; GDI must report
            # the availability problem before asking for a snapshot.
            raise BackendUnavailableError("window client area is empty or minimized")
        snapshot = self._windows.snapshot(self._target.hwnd)
        if snapshot.identity != self._target:
            raise ContractViolation("window identity changed during capture")
        capture_rect = snapshot.visible_screen_rect or snapshot.client_screen_rect
        width = int(capture_rect.width)
        height = int(capture_rect.height)
        if width < 1 or height < 1:
            raise BackendUnavailableError("visible window area is empty or minimized")
        timestamp = self._clock.now()
        payload = self._capture_bgra(
            self._target.hwnd,
            width,
            height,
            source_x=int(capture_rect.left - snapshot.window_rect.left),
            source_y=int(capture_rect.top - snapshot.window_rect.top),
        )
        frame_id = uuid.uuid4().hex
        return Frame(
            frame_id=frame_id,
            capture_timestamp=timestamp,
            present_estimate=None,
            window_identity=self._target,
            width=width,
            height=height,
            stride_bytes=width * 4,
            pixel_format=PixelFormat.BGRA8,
            physical_rect=capture_rect,
            client_rect=Rect(0, 0, width, height),
            source_backend=self.backend_id,
            buffer_handle=BufferHandle(
                handle_id=f"cpu:{frame_id}",
                kind=BufferKind.CPU_BYTES,
                size_bytes=len(payload),
                payload=payload,
            ),
        )

    def _stop(self) -> None:
        return None

    def _configure_signatures(self) -> None:
        self._user32.GetWindowDC.argtypes = [ctypes.c_void_p]
        self._user32.GetWindowDC.restype = ctypes.c_void_p
        self._user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._user32.ReleaseDC.restype = ctypes.c_int
        self._gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        self._gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        self._gdi32.CreateDIBSection.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_BITMAPINFO),
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        self._gdi32.CreateDIBSection.restype = ctypes.c_void_p
        self._gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._gdi32.SelectObject.restype = ctypes.c_void_p
        self._gdi32.BitBlt.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self._gdi32.BitBlt.restype = ctypes.c_bool
        self._gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        self._gdi32.DeleteObject.restype = ctypes.c_bool
        self._gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
        self._gdi32.DeleteDC.restype = ctypes.c_bool

    def _capture_bgra(
        self,
        hwnd: int,
        width: int,
        height: int,
        *,
        source_x: int,
        source_y: int,
    ) -> bytes:
        window_dc = self._user32.GetWindowDC(ctypes.c_void_p(hwnd))
        if not window_dc:
            raise ctypes.WinError(ctypes.get_last_error())
        memory_dc = None
        bitmap = None
        previous = None
        try:
            memory_dc = self._gdi32.CreateCompatibleDC(window_dc)
            if not memory_dc:
                raise ctypes.WinError(ctypes.get_last_error())
            header = _BITMAPINFOHEADER(
                ctypes.sizeof(_BITMAPINFOHEADER),
                width,
                -height,
                1,
                32,
                self._BI_RGB,
                width * height * 4,
                0,
                0,
                0,
                0,
            )
            bitmap_info = _BITMAPINFO(header)
            bits = ctypes.c_void_p()
            bitmap = self._gdi32.CreateDIBSection(
                window_dc,
                ctypes.byref(bitmap_info),
                self._DIB_RGB_COLORS,
                ctypes.byref(bits),
                None,
                0,
            )
            if not bitmap or not bits.value:
                raise ctypes.WinError(ctypes.get_last_error())
            previous = self._gdi32.SelectObject(memory_dc, bitmap)
            if not previous:
                raise ctypes.WinError(ctypes.get_last_error())
            copied = self._gdi32.BitBlt(
                memory_dc,
                0,
                0,
                width,
                height,
                window_dc,
                source_x,
                source_y,
                self._SRCCOPY | self._CAPTUREBLT,
            )
            if not copied:
                raise ctypes.WinError(ctypes.get_last_error())
            return ctypes.string_at(bits, width * height * 4)
        finally:
            if previous and memory_dc:
                self._gdi32.SelectObject(memory_dc, previous)
            if bitmap:
                self._gdi32.DeleteObject(bitmap)
            if memory_dc:
                self._gdi32.DeleteDC(memory_dc)
            self._user32.ReleaseDC(ctypes.c_void_p(hwnd), window_dc)
