from __future__ import annotations

import ctypes
import os
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
    _user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.UpdateWindow.argtypes = [ctypes.c_void_p]
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
    style = 0x00CF0000 | 0x10000000  # WS_OVERLAPPEDWINDOW | WS_VISIBLE
    hwnd = _user32.CreateWindowExW(
        0,
        "STATIC",
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
    try:
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
        hwnd_value = int(hwnd)
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
        _user32.DestroyWindow(hwnd)
