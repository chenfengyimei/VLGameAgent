from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.windows.coordinates import Rect
from uga.windows.dpi import dpi_for_window
from uga.windows.window_identity import WindowIdentity, WindowIdentityTracker


@dataclass(frozen=True, slots=True)
class WindowSnapshot:
    identity: WindowIdentity
    title: str
    window_rect: Rect
    client_screen_rect: Rect
    dpi: int
    is_visible: bool
    is_foreground: bool


@runtime_checkable
class WindowBackend(Protocol):
    def discover(self, *, executable_name: str | None = None) -> tuple[WindowSnapshot, ...]: ...

    def snapshot(self, hwnd: int) -> WindowSnapshot: ...

    def foreground_hwnd(self) -> int | None: ...


if os.name == "nt":
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class _POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class Win32WindowBackend:
    """ctypes Win32 discovery isolated behind the stable WindowBackend protocol."""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SW_RESTORE = 9

    def __init__(self, tracker: WindowIdentityTracker | None = None) -> None:
        if os.name != "nt":
            raise BackendUnavailableError("Win32WindowBackend requires Windows")
        self._tracker = tracker or WindowIdentityTracker()
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_signatures()

    def foreground_hwnd(self) -> int | None:
        hwnd = int(self._user32.GetForegroundWindow() or 0)
        return hwnd or None

    def request_foreground(self, hwnd: int) -> bool:
        """Restore and request focus for an already validated target window."""
        self.snapshot(hwnd)
        foreground = int(self._user32.GetForegroundWindow() or 0)
        current_thread = int(self._kernel32.GetCurrentThreadId())
        foreground_thread = (
            int(self._user32.GetWindowThreadProcessId(ctypes.c_void_p(foreground), None))
            if foreground
            else 0
        )
        target_thread = int(
            self._user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), None)
        )
        attached: list[int] = []
        try:
            for thread_id in (foreground_thread, target_thread):
                if (
                    thread_id
                    and thread_id != current_thread
                    and thread_id not in attached
                    and self._user32.AttachThreadInput(current_thread, thread_id, True)
                ):
                    attached.append(thread_id)
            self._user32.ShowWindow(ctypes.c_void_p(hwnd), self._SW_RESTORE)
            self._user32.BringWindowToTop(ctypes.c_void_p(hwnd))
            self._user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
            self._user32.SetFocus(ctypes.c_void_p(hwnd))
            if self.foreground_hwnd() != hwnd:
                self._user32.SwitchToThisWindow(ctypes.c_void_p(hwnd), True)
        finally:
            for thread_id in reversed(attached):
                self._user32.AttachThreadInput(current_thread, thread_id, False)
        return self.foreground_hwnd() == hwnd

    def discover(self, *, executable_name: str | None = None) -> tuple[WindowSnapshot, ...]:
        handles: list[int] = []
        # Win32 BOOL is a 32-bit integer; c_bool is only one byte and corrupts
        # the callback ABI on 64-bit Windows.
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

        @callback_type  # type: ignore[untyped-decorator]
        def collect(hwnd: int, _lparam: int) -> int:
            if self._user32.IsWindowVisible(hwnd) and self._user32.GetWindowTextLengthW(hwnd) > 0:
                handles.append(int(hwnd))
            return 1

        ctypes.set_last_error(0)
        enumerated = self._user32.EnumWindows(collect, 0)
        error_code = ctypes.get_last_error()
        if not enumerated and error_code:
            raise ctypes.WinError(error_code)
        snapshots: list[WindowSnapshot] = []
        wanted = executable_name.casefold() if executable_name else None
        for hwnd in handles:
            try:
                snapshot = self.snapshot(hwnd)
                if wanted is None or self._executable_name(hwnd).casefold() == wanted:
                    snapshots.append(snapshot)
            except (OSError, ContractViolation):
                # A window may disappear between enumeration and inspection.
                continue
        return tuple(snapshots)

    def snapshot(self, hwnd: int) -> WindowSnapshot:
        if hwnd <= 0 or not self._user32.IsWindow(hwnd):
            raise ContractViolation(f"invalid or stale HWND: {hwnd}")
        pid = ctypes.c_ulong()
        self._user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        path, start_time = self._process_metadata(int(pid.value))
        identity = self._tracker.identify(
            hwnd=hwnd,
            pid=int(pid.value),
            executable_path=path,
            process_start_time_100ns=start_time,
        )
        return WindowSnapshot(
            identity=identity,
            title=self._window_title(hwnd),
            window_rect=self._window_rect(hwnd),
            client_screen_rect=self._client_rect(hwnd),
            dpi=dpi_for_window(hwnd),
            is_visible=bool(self._user32.IsWindowVisible(ctypes.c_void_p(hwnd))),
            is_foreground=self.foreground_hwnd() == hwnd,
        )

    def _window_title(self, hwnd: int) -> str:
        length = int(self._user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd)))
        buffer = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(ctypes.c_void_p(hwnd), buffer, len(buffer))
        return buffer.value

    def _window_rect(self, hwnd: int) -> Rect:
        rect = _RECT()
        if not self._user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Rect(rect.left, rect.top, rect.right, rect.bottom)

    def _client_rect(self, hwnd: int) -> Rect:
        rect = _RECT()
        if not self._user32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        origin = _POINT(0, 0)
        if not self._user32.ClientToScreen(ctypes.c_void_p(hwnd), ctypes.byref(origin)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Rect(
            origin.x,
            origin.y,
            origin.x + rect.right - rect.left,
            origin.y + rect.bottom - rect.top,
        )

    def _executable_name(self, hwnd: int) -> str:
        pid = ctypes.c_ulong()
        self._user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        path, _ = self._process_metadata(int(pid.value))
        return os.path.basename(path)

    def _process_metadata(self, pid: int) -> tuple[str, int]:
        handle = self._kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            capacity = ctypes.c_ulong(32768)
            buffer = ctypes.create_unicode_buffer(capacity.value)
            if not self._kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(capacity)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            created = _FILETIME()
            exited = _FILETIME()
            kernel = _FILETIME()
            user = _FILETIME()
            if not self._kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            start = (int(created.high) << 32) | int(created.low)
            return buffer.value, start
        finally:
            self._kernel32.CloseHandle(handle)

    def _configure_signatures(self) -> None:
        """Prevent pointer truncation by declaring all 64-bit Win32 signatures."""
        self._user32.GetForegroundWindow.restype = ctypes.c_void_p
        self._user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._user32.ShowWindow.restype = ctypes.c_bool
        self._user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        self._user32.SetForegroundWindow.restype = ctypes.c_bool
        self._user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
        self._user32.BringWindowToTop.restype = ctypes.c_bool
        self._user32.SetFocus.argtypes = [ctypes.c_void_p]
        self._user32.SetFocus.restype = ctypes.c_void_p
        self._user32.SwitchToThisWindow.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        self._user32.SwitchToThisWindow.restype = None
        self._user32.AttachThreadInput.argtypes = [
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_bool,
        ]
        self._user32.AttachThreadInput.restype = ctypes.c_bool
        self._user32.IsWindow.argtypes = [ctypes.c_void_p]
        self._user32.IsWindow.restype = ctypes.c_bool
        self._user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        self._user32.IsWindowVisible.restype = ctypes.c_bool
        self._user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        self._user32.GetWindowTextLengthW.restype = ctypes.c_int
        self._user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        self._user32.GetWindowTextW.restype = ctypes.c_int
        self._user32.GetWindowThreadProcessId.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        self._user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
        self._user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
        self._user32.GetWindowRect.restype = ctypes.c_bool
        self._user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT)]
        self._user32.GetClientRect.restype = ctypes.c_bool
        self._user32.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.POINTER(_POINT)]
        self._user32.ClientToScreen.restype = ctypes.c_bool
        self._kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
        self._kernel32.OpenProcess.restype = ctypes.c_void_p
        self._kernel32.GetCurrentThreadId.argtypes = []
        self._kernel32.GetCurrentThreadId.restype = ctypes.c_ulong
        self._kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        self._kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
        filetime_pointer = ctypes.POINTER(_FILETIME)
        self._kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            filetime_pointer,
            filetime_pointer,
            filetime_pointer,
            filetime_pointer,
        ]
        self._kernel32.GetProcessTimes.restype = ctypes.c_bool
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_bool
