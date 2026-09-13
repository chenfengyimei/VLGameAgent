from __future__ import annotations

import ctypes
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from uga.windows.backend import Win32WindowBackend
from uga.windows.window_identity import WindowIdentity, executable_path_hash

if os.name == "nt":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _user32.CreateWindowExW.restype = ctypes.c_void_p
    _user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.SetWindowPos.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.SetWindowTextW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _user32.SetWindowTextW.restype = wintypes.BOOL
    _user32.UpdateWindow.argtypes = [ctypes.c_void_p]
    _user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        ctypes.c_void_p,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    _user32.PeekMessageW.restype = wintypes.BOOL
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.TranslateMessage.restype = wintypes.BOOL
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.restype = ctypes.c_ssize_t
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.GetSystemMetrics.restype = ctypes.c_int
    _user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = ctypes.c_void_p
    _user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    _user32.MonitorFromPoint.restype = ctypes.c_void_p
    _user32.GetDC.argtypes = [ctypes.c_void_p]
    _user32.GetDC.restype = ctypes.c_void_p
    _user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
    _gdi32.CreateSolidBrush.restype = ctypes.c_void_p
    _gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    _user32.FillRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT), ctypes.c_void_p]
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = ctypes.c_void_p


@contextmanager
def capture_test_window(
    windows: Win32WindowBackend | None = None,
) -> Iterator[tuple[int, WindowIdentity]]:
    if os.name != "nt":
        raise RuntimeError("Windows-only fixture")
    ready = threading.Event()
    stop = threading.Event()
    handles: list[int] = []
    failures: list[BaseException] = []

    def run_window() -> None:
        try:
            style = 0x00CF0000 | 0x10000000  # WS_OVERLAPPEDWINDOW | WS_VISIBLE
            # A STATIC-class window hit-tests transparent, which makes
            # WindowFromPoint see through it. BUTTON owns its visible points.
            hwnd = _user32.CreateWindowExW(
                0,
                "BUTTON",
                "UGA Native Capture Fixture",
                style,
                100,
                100,
                320,
                240,
                None,
                None,
                _kernel32.GetModuleHandleW(None),
                None,
            )
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(int(hwnd))
            _user32.ShowWindow(hwnd, 5)
            _user32.UpdateWindow(hwnd)
            dc = _user32.GetDC(hwnd)
            brush = _gdi32.CreateSolidBrush(0x0020A0E0)
            try:
                rect = wintypes.RECT(0, 0, 320, 240)
                _user32.FillRect(dc, ctypes.byref(rect), brush)
            finally:
                _gdi32.DeleteObject(brush)
                _user32.ReleaseDC(hwnd, dc)
            ready.set()
            message = wintypes.MSG()
            while not stop.wait(0.01):
                while _user32.PeekMessageW(
                    ctypes.byref(message), None, 0, 0, 0x0001
                ):
                    _user32.TranslateMessage(ctypes.byref(message))
                    _user32.DispatchMessageW(ctypes.byref(message))
        except BaseException as exc:  # noqa: BLE001 - propagate fixture startup
            failures.append(exc)
            ready.set()
        finally:
            if handles:
                _user32.DestroyWindow(ctypes.c_void_p(handles[0]))

    window_thread = threading.Thread(
        target=run_window,
        name="uga-capture-test-window",
        daemon=True,
    )
    window_thread.start()
    if not ready.wait(5.0):
        stop.set()
        window_thread.join(5.0)
        raise RuntimeError("capture test window did not start within five seconds")
    if failures:
        stop.set()
        window_thread.join(5.0)
        raise failures[0]
    hwnd_value = handles[0]
    try:
        identity = (
            windows.snapshot(hwnd_value).identity
            if windows is not None
            else WindowIdentity(
                hwnd=hwnd_value,
                pid=os.getpid(),
                executable_path_hash=executable_path_hash(os.path.abspath(os.sys.executable)),
                process_start_time_100ns=1,
                window_generation=1,
            )
        )
        yield hwnd_value, identity
    finally:
        stop.set()
        window_thread.join(5.0)
        if window_thread.is_alive():
            raise RuntimeError("capture test window thread did not stop")


_SW_MINIMIZE = 6
_SW_RESTORE = 9
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_MONITOR_DEFAULTTONEAREST = 2
_SM_CXSCREEN = 0
_SM_YVIRTUALSCREEN = 1
_SM_XVIRTUALSCREEN = 76
_SM_CMONITORS = 80


def resize_window(hwnd: int, width: int, height: int) -> None:
    """Resizes the outer window rect, keeping the current position."""
    ok = _user32.SetWindowPos(
        ctypes.c_void_p(hwnd),
        None,
        0,
        0,
        width,
        height,
        _SWP_NOMOVE | _SWP_NOZORDER | _SWP_NOACTIVATE,
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def refresh_window(hwnd: int, sequence: int = 0) -> None:
    """Forces a new compositor frame after a capture session attaches."""
    ok = _user32.SetWindowTextW(
        ctypes.c_void_p(hwnd),
        f"UGA Native Capture Fixture {sequence}",
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    _user32.UpdateWindow(ctypes.c_void_p(hwnd))


def move_window(hwnd: int, x: int, y: int) -> None:
    """Moves the window without resizing or changing z-order."""
    ok = _user32.SetWindowPos(
        ctypes.c_void_p(hwnd),
        None,
        x,
        y,
        0,
        0,
        _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE,
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def raise_window(hwnd: int) -> None:
    """Places the window in the topmost band so nothing can cover it."""
    ok = _user32.SetWindowPos(
        ctypes.c_void_p(hwnd),
        ctypes.c_void_p(-1),
        0,
        0,
        0,
        0,
        _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE,
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def minimize_window(hwnd: int) -> None:
    _user32.ShowWindow(ctypes.c_void_p(hwnd), _SW_MINIMIZE)


def restore_window(hwnd: int) -> None:
    _user32.ShowWindow(ctypes.c_void_p(hwnd), _SW_RESTORE)


def monitor_of_window(hwnd: int) -> int:
    return int(
        _user32.MonitorFromWindow(ctypes.c_void_p(hwnd), _MONITOR_DEFAULTTONEAREST) or 0
    )


def monitor_count() -> int:
    return int(_user32.GetSystemMetrics(_SM_CMONITORS))


def distinct_monitor_position(hwnd: int) -> tuple[int, int] | None:
    """Finds a position on a monitor other than the window's current one."""
    current = monitor_of_window(hwnd)
    virtual_x = int(_user32.GetSystemMetrics(_SM_XVIRTUALSCREEN))
    virtual_y = int(_user32.GetSystemMetrics(_SM_YVIRTUALSCREEN))
    primary_width = int(_user32.GetSystemMetrics(_SM_CXSCREEN))
    candidates = ((virtual_x + 60, virtual_y + 60), (primary_width + 60, 60))
    for x, y in candidates:
        point = wintypes.POINT(x, y)
        monitor = int(_user32.MonitorFromPoint(point, _MONITOR_DEFAULTTONEAREST) or 0)
        if monitor and monitor != current:
            return (x, y)
    return None
