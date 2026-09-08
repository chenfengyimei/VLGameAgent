from __future__ import annotations

import ctypes
import os
import uuid
from enum import IntEnum
from pathlib import Path

from uga.capture.base import CaptureCapability, GraphicsAPI, WindowMode
from uga.capture.frame import BufferHandle, BufferKind, PixelFormat
from uga.capture.native_adapter import NativeCapturedFrame, NativeCaptureDriver
from uga.core.errors import (
    BackendStateError,
    BackendUnavailableError,
    CaptureAccessLostError,
    CaptureTimeoutError,
)
from uga.time.clock import UGATime
from uga.windows.coordinates import Rect
from uga.windows.window_identity import WindowIdentity

_EXPECTED_ABI = 0x0001_0001


class NativeBackendId(IntEnum):
    WGC = 1
    DXGI = 2


class _NativeFrame(ctypes.Structure):
    _fields_ = [
        ("capture_timestamp_ns", ctypes.c_int64),
        ("present_estimate_ns", ctypes.c_int64),
        ("has_present_estimate", ctypes.c_uint8),
        ("_padding", ctypes.c_uint8 * 7),
        ("left", ctypes.c_int32),
        ("top", ctypes.c_int32),
        ("right", ctypes.c_int32),
        ("bottom", ctypes.c_int32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("stride_bytes", ctypes.c_uint32),
        ("data", ctypes.POINTER(ctypes.c_uint8)),
        ("data_len", ctypes.c_size_t),
    ]


def native_library_candidates() -> tuple[Path, ...]:
    override = os.environ.get("UGA_NATIVE_CAPTURE_DLL")
    root = Path(__file__).resolve().parents[2]
    candidates = []
    if override:
        candidates.append(Path(override).expanduser())
    candidates.extend(
        [
            root / "native" / "target" / "release" / "uga_capture.dll",
            root / "native" / "target" / "debug" / "uga_capture.dll",
        ]
    )
    return tuple(candidates)


def find_native_library() -> Path | None:
    if os.name != "nt":
        return None
    return next((path for path in native_library_candidates() if path.is_file()), None)


class NativeCaptureLibrary:
    """Validated ctypes wrapper for the panic-contained UGA capture C ABI."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self._dll = ctypes.CDLL(str(self.path))
        self._configure()
        abi = int(self._dll.uga_capture_abi_version())
        if abi != _EXPECTED_ABI:
            raise BackendUnavailableError(
                f"native capture ABI mismatch: expected {_EXPECTED_ABI:#x}, got {abi:#x}"
            )

    def _configure(self) -> None:
        self._dll.uga_capture_abi_version.argtypes = []
        self._dll.uga_capture_abi_version.restype = ctypes.c_uint32
        self._dll.uga_capture_create.argtypes = [
            ctypes.c_uint32,
            ctypes.c_ssize_t,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._dll.uga_capture_create.restype = ctypes.c_int32
        self._dll.uga_capture_next.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(_NativeFrame),
        ]
        self._dll.uga_capture_next.restype = ctypes.c_int32
        self._dll.uga_capture_frame_release.argtypes = [ctypes.POINTER(_NativeFrame)]
        self._dll.uga_capture_frame_release.restype = None
        self._dll.uga_capture_destroy.argtypes = [ctypes.c_void_p]
        self._dll.uga_capture_destroy.restype = None
        self._dll.uga_capture_last_error.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        self._dll.uga_capture_last_error.restype = ctypes.c_size_t

    def last_error(self) -> str:
        required = int(self._dll.uga_capture_last_error(None, 0))
        buffer = ctypes.create_string_buffer(required + 1)
        self._dll.uga_capture_last_error(buffer, len(buffer))
        return buffer.value.decode("utf-8", errors="replace")

    def create(self, backend: NativeBackendId, hwnd: int) -> ctypes.c_void_p:
        handle = ctypes.c_void_p()
        status = int(self._dll.uga_capture_create(int(backend), hwnd, ctypes.byref(handle)))
        if status != 0 or not handle.value:
            self.raise_status(status)
        return handle

    def next(self, handle: ctypes.c_void_p, timeout_ms: int) -> NativeCapturedFrame:
        native = _NativeFrame()
        status = int(self._dll.uga_capture_next(handle, timeout_ms, ctypes.byref(native)))
        if status != 0:
            self.raise_status(status)
        try:
            payload = ctypes.string_at(native.data, native.data_len)
            frame_id = uuid.uuid4().hex
            return NativeCapturedFrame(
                frame_id=frame_id,
                capture_timestamp=UGATime(int(native.capture_timestamp_ns)),
                present_estimate=(
                    UGATime(int(native.present_estimate_ns))
                    if native.has_present_estimate
                    else None
                ),
                width=int(native.width),
                height=int(native.height),
                stride_bytes=int(native.stride_bytes),
                pixel_format=PixelFormat.BGRA8,
                physical_rect=Rect(native.left, native.top, native.right, native.bottom),
                client_rect=Rect(0, 0, native.width, native.height),
                buffer_handle=BufferHandle(
                    handle_id=f"native-copy:{frame_id}",
                    kind=BufferKind.CPU_BYTES,
                    size_bytes=len(payload),
                    payload=payload,
                ),
            )
        finally:
            self._dll.uga_capture_frame_release(ctypes.byref(native))

    def destroy(self, handle: ctypes.c_void_p) -> None:
        self._dll.uga_capture_destroy(handle)

    def raise_status(self, status: int) -> None:
        detail = self.last_error() or f"native capture status {status}"
        if status == 3:
            raise CaptureTimeoutError(detail)
        if status == 4:
            raise CaptureAccessLostError(detail)
        if status == 5:
            raise BackendUnavailableError(f"target lost: {detail}")
        raise BackendUnavailableError(detail)


class CtypesNativeCaptureDriver(NativeCaptureDriver):
    def __init__(
        self,
        backend: NativeBackendId,
        library: NativeCaptureLibrary,
        timeout_ms: int = 250,
    ) -> None:
        self._backend = backend
        self._library = library
        self._timeout_ms = timeout_ms
        self._handle: ctypes.c_void_p | None = None

    def probe(self, target: WindowIdentity) -> tuple[bool, str, CaptureCapability]:
        capability = _capability(self._backend)
        try:
            handle = self._library.create(self._backend, target.hwnd)
        except BackendUnavailableError as error:
            return False, str(error), capability
        self._library.destroy(handle)
        return True, f"native ABI {_EXPECTED_ABI:#x} available", capability

    def start(self, target: WindowIdentity) -> None:
        if self._handle is not None:
            raise BackendStateError("native capture driver is already started")
        self._handle = self._library.create(self._backend, target.hwnd)

    def capture(self) -> NativeCapturedFrame:
        if self._handle is None:
            raise BackendStateError("native capture driver is not started")
        return self._library.next(self._handle, self._timeout_ms)

    def stop(self) -> None:
        if self._handle is not None:
            self._library.destroy(self._handle)
            self._handle = None


def load_default_driver(backend: NativeBackendId) -> CtypesNativeCaptureDriver | None:
    path = find_native_library()
    if path is None:
        return None
    try:
        return CtypesNativeCaptureDriver(backend, NativeCaptureLibrary(path))
    except (OSError, BackendUnavailableError):
        return None


def _capability(backend: NativeBackendId) -> CaptureCapability:
    if backend == NativeBackendId.WGC:
        return CaptureCapability(
            window_modes=frozenset({WindowMode.WINDOWED, WindowMode.BORDERLESS}),
            graphics_apis=frozenset(
                {GraphicsAPI.DX11, GraphicsAPI.DX12, GraphicsAPI.VULKAN, GraphicsAPI.OPENGL}
            ),
            pixel_formats=frozenset({PixelFormat.BGRA8}),
            supports_multi_monitor=True,
            supports_hdr=False,
            supports_resize=True,
            supports_minimized=False,
        )
    return CaptureCapability(
        window_modes=frozenset(
            {WindowMode.WINDOWED, WindowMode.BORDERLESS, WindowMode.EXCLUSIVE_FULLSCREEN}
        ),
        graphics_apis=frozenset(
            {GraphicsAPI.DX11, GraphicsAPI.DX12, GraphicsAPI.VULKAN, GraphicsAPI.OPENGL}
        ),
        pixel_formats=frozenset({PixelFormat.BGRA8}),
        supports_multi_monitor=False,
        supports_hdr=False,
        supports_resize=True,
        supports_minimized=False,
    )
