from __future__ import annotations

import ctypes
import os

from uga.core.errors import BackendUnavailableError

DEFAULT_DPI = 96


def enable_per_monitor_v2_awareness() -> bool:
    """Enable per-monitor-v2 awareness when Windows permits the process change."""
    if os.name != "nt":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    setter = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if setter is None:
        return False
    setter.argtypes = [ctypes.c_void_p]
    setter.restype = ctypes.c_bool
    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is the signed pseudo-handle -4.
    return bool(setter(ctypes.c_void_p(-4)))


def dpi_for_window(hwnd: int) -> int:
    if os.name != "nt":
        raise BackendUnavailableError("window DPI is only available on Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    getter = getattr(user32, "GetDpiForWindow", None)
    if getter is None:
        return DEFAULT_DPI
    getter.argtypes = [ctypes.c_void_p]
    getter.restype = ctypes.c_uint
    dpi = int(getter(ctypes.c_void_p(hwnd)))
    return dpi or DEFAULT_DPI


def dpi_scale(dpi: int) -> float:
    if dpi <= 0:
        raise ValueError("DPI must be positive")
    return dpi / DEFAULT_DPI
