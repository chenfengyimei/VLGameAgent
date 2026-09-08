from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from threading import Event, Lock, Thread

from uga.core.errors import BackendUnavailableError
from uga.safety.shutdown import SafetyShutdown, SafetyTrip, ShutdownCause


class EmergencyStop:
    """Non-resettable runtime latch. A new runtime instance is required to resume."""

    def __init__(self, shutdown: SafetyShutdown) -> None:
        self._shutdown = shutdown

    def trigger(self) -> SafetyTrip:
        return self._shutdown.trip(ShutdownCause.EMERGENCY_HOTKEY)

    @property
    def tripped(self) -> SafetyTrip | None:
        return self._shutdown.tripped


class Win32EmergencyHotkey:
    """OS-level Ctrl+Shift+F12 listener isolated from agent planning code."""

    _WM_HOTKEY = 0x0312
    _WM_QUIT = 0x0012
    _MOD_SHIFT = 0x0004
    _MOD_CONTROL = 0x0002
    _MOD_NOREPEAT = 0x4000
    _VK_F12 = 0x7B
    _HOTKEY_ID = 0x554741

    def __init__(self, callback: Callable[[], object]) -> None:
        if os.name != "nt":
            raise BackendUnavailableError("global emergency hotkey requires Windows")
        self._callback = callback
        self._started = Event()
        self._stopped = Event()
        self._failure: BaseException | None = None
        self._thread_id = 0
        self._lock = Lock()
        self._thread = Thread(target=self._message_loop, name="uga-emergency-hotkey", daemon=True)

    def start(self, timeout_s: float = 5.0) -> None:
        with self._lock:
            if self._thread.is_alive():
                return
            self._thread.start()
        if not self._started.wait(timeout_s):
            raise TimeoutError("emergency hotkey listener did not start")
        if self._failure is not None:
            raise RuntimeError("failed to register emergency hotkey") from self._failure

    def close(self) -> None:
        if not self._thread.is_alive() or not self._thread_id:
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostThreadMessageW(self._thread_id, self._WM_QUIT, 0, 0)
        self._thread.join(timeout=5.0)

    def _message_loop(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self._thread_id = int(kernel32.GetCurrentThreadId())
        modifiers = self._MOD_CONTROL | self._MOD_SHIFT | self._MOD_NOREPEAT
        try:
            if not user32.RegisterHotKey(None, self._HOTKEY_ID, modifiers, self._VK_F12):
                raise ctypes.WinError(ctypes.get_last_error())
            self._started.set()
            message = wintypes.MSG()
            while True:
                result = int(user32.GetMessageW(ctypes.byref(message), None, 0, 0))
                if result == 0:
                    break
                if result < 0:
                    raise ctypes.WinError(ctypes.get_last_error())
                if message.message == self._WM_HOTKEY and message.wParam == self._HOTKEY_ID:
                    self._callback()
        except BaseException as exc:
            self._failure = exc
            self._started.set()
        finally:
            user32.UnregisterHotKey(None, self._HOTKEY_ID)
            self._stopped.set()
