from __future__ import annotations

import atexit
import ctypes
import hashlib
import hmac
import os
import re
import shutil
import tempfile
import uuid
from enum import IntEnum
from pathlib import Path
from threading import RLock

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
from uga.windows.backend import WindowBackend
from uga.windows.coordinates import Rect
from uga.windows.window_identity import WindowIdentity

_EXPECTED_ABI = 0x0001_0001
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_DLL_SEARCH = 0x00000100 | 0x00000800


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

    def __init__(self, path: str | Path, *, expected_sha256: str) -> None:
        source = Path(path).resolve()
        if _SHA256.fullmatch(expected_sha256) is None:
            raise BackendUnavailableError("native capture requires a trusted SHA-256 digest")
        if not source.is_file():
            raise BackendUnavailableError("native capture library is not a regular file")
        # Hash and load one private copy so the verified bytes are the loaded
        # bytes; an attacker with write access to the source directory cannot
        # race the digest check with a swapped module.
        self._private_dir = tempfile.mkdtemp(prefix="uga-native-verify-")
        self.path = Path(self._private_dir) / source.name
        atexit.register(self._cleanup_private_copy)
        try:
            shutil.copyfile(source, self.path)
            digest = hashlib.sha256()
            with self.path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
                raise BackendUnavailableError("native capture library SHA-256 mismatch")
            self._dll = ctypes.CDLL(str(self.path), winmode=_SAFE_DLL_SEARCH)
        except OSError as exc:
            raise BackendUnavailableError("native capture library cannot be verified") from exc
        self._configure()
        abi = int(self._dll.uga_capture_abi_version())
        if abi != _EXPECTED_ABI:
            raise BackendUnavailableError(
                f"native capture ABI mismatch: expected {_EXPECTED_ABI:#x}, got {abi}"
            )

    def _cleanup_private_copy(self) -> None:
        # A loaded DLL stays mapped on Windows for the process lifetime, so
        # successful loads keep their copy until exit; failed loads release
        # their copy here. Copies live in a per-user temp directory.
        shutil.rmtree(self._private_dir, ignore_errors=True)

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
        if not 0 <= required <= 65_536:
            return "native error detail exceeded the bounded buffer"
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
            width, height, stride = int(native.width), int(native.height), int(native.stride_bytes)
            if (
                not native.data or not 1 <= width <= 8192 or not 1 <= height <= 8192
                or width * height > 33_554_432 or stride < width * 4
                or not stride * height <= int(native.data_len) <= 256 * 1024 * 1024
            ):
                raise BackendUnavailableError("native frame buffer geometry is invalid")
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
        detail = self.last_error()
        if detail:
            raise BackendUnavailableError(detail)

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
        windows: WindowBackend,
        timeout_ms: int = 250,
    ) -> None:
        if (
            isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int)
            or not 1 <= timeout_ms <= 10_000
        ):
            raise BackendUnavailableError("native timeout must be within [1, 10000] ms")
        self._lock = RLock()
        self._backend = backend
        self._library = library
        self._windows = windows
        self._timeout_ms = timeout_ms
        self._handle: ctypes.c_void_p | None = None
        self._target: WindowIdentity | None = None

    def probe(self, target: WindowIdentity) -> tuple[bool, str, CaptureCapability]:
        with self._lock:
            capability = _capability(self._backend)
            try:
                self._verify_target(target)
                handle = self._library.create(self._backend, target.hwnd)
                self._verify_target(target)
            except BackendUnavailableError as error:
                return False, str(error), capability
            finally:
                if "handle" in locals():
                    self._library.destroy(handle)
            return True, f"native ABI {_EXPECTED_ABI:#x} available", capability

    def start(self, target: WindowIdentity) -> None:
        with self._lock:
            if self._handle is not None:
                raise BackendStateError("native capture driver is already started")
            self._verify_target(target)
            handle = self._library.create(self._backend, target.hwnd)
            try:
                self._verify_target(target)
            except BaseException:
                self._library.destroy(handle)
                raise
            self._target = target
            self._handle = handle

    def capture(self) -> NativeCapturedFrame:
        with self._lock:
            if self._handle is None or self._target is None:
                raise BackendStateError("native capture driver is not started")
            try:
                self._verify_target(self._target)
            except BaseException:
                self._invalidate_session()
                raise
            frame = self._library.next(self._handle, self._timeout_ms)
            try:
                self._verify_target(self._target)
            except BaseException:
                self._invalidate_session()
                raise
            return frame

    def stop(self) -> None:
        with self._lock:
            self._invalidate_session()

    def _invalidate_session(self) -> None:
        handle = self._handle
        self._handle = None
        self._target = None
        if handle is not None:
            self._library.destroy(handle)

    def _verify_target(self, expected: WindowIdentity) -> None:
        try:
            actual = self._windows.snapshot(expected.hwnd).identity
        except Exception as exc:
            raise CaptureAccessLostError("native capture target identity is unavailable") from exc
        if actual != expected:
            raise CaptureAccessLostError("native capture target identity changed")


def load_default_driver(
    backend: NativeBackendId,
    windows: WindowBackend | None = None,
    *,
    expected_sha256: str | None = None,
) -> CtypesNativeCaptureDriver | None:
    if windows is None:
        return None
    env_path = os.environ.get("UGA_NATIVE_CAPTURE_DLL")
    env_digest = os.environ.get("UGA_NATIVE_CAPTURE_SHA256")
    trusted_digest = expected_sha256 or env_digest
    explicit = expected_sha256 is not None or bool(env_path) or bool(env_digest)
    if not explicit:
        # Implicit discovery never loads native code without an explicit pin.
        return None
    # A launcher or test that explicitly pins the native library must never
    # degrade silently: verification failures abort instead of reporting the
    # provider as "not installed".
    path = find_native_library()
    if path is None:
        raise BackendUnavailableError(
            "explicit native capture library is missing: "
            f"{env_path or 'no UGA_NATIVE_CAPTURE_DLL override found'}"
        )
    if trusted_digest is None:
        raise BackendUnavailableError("explicit native capture requires a trusted SHA-256 digest")
    return CtypesNativeCaptureDriver(
        backend,
        NativeCaptureLibrary(path, expected_sha256=trusted_digest),
        windows,
    )


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
