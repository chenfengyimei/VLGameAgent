from __future__ import annotations

import ctypes
import os

from uga.control.input_state import KeyboardStateMachine, KeyId
from uga.control.physical import (
    AbsolutePointerAction,
    GamepadAction,
    KeyboardAction,
    KeyEncoding,
    MouseButton,
    MouseButtonAction,
    PhysicalAction,
    RawInputAction,
    RelativeMouseAction,
    UnicodeTextAction,
    WheelAction,
)
from uga.core.errors import BackendUnavailableError, ContractViolation

if os.name == "nt":
    from ctypes import wintypes

    _ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _anonymous_ = ("value",)
        _fields_ = [("type", wintypes.DWORD), ("value", _INPUT_UNION)]


class SendInputBackend:
    """Stateful Win32 keyboard/mouse backend; callers must use InputExecutor."""

    _INPUT_MOUSE = 0
    _INPUT_KEYBOARD = 1
    _KEYEVENTF_EXTENDEDKEY = 0x0001
    _KEYEVENTF_KEYUP = 0x0002
    _KEYEVENTF_SCANCODE = 0x0008
    _KEYEVENTF_UNICODE = 0x0004
    _MOUSEEVENTF_MOVE = 0x0001
    _MOUSEEVENTF_LEFTDOWN = 0x0002
    _MOUSEEVENTF_LEFTUP = 0x0004
    _MOUSEEVENTF_RIGHTDOWN = 0x0008
    _MOUSEEVENTF_RIGHTUP = 0x0010
    _MOUSEEVENTF_MIDDLEDOWN = 0x0020
    _MOUSEEVENTF_MIDDLEUP = 0x0040
    _MOUSEEVENTF_XDOWN = 0x0080
    _MOUSEEVENTF_XUP = 0x0100
    _MOUSEEVENTF_WHEEL = 0x0800
    _MOUSEEVENTF_VIRTUALDESK = 0x4000
    _MOUSEEVENTF_ABSOLUTE = 0x8000
    _SM_XVIRTUALSCREEN = 76
    _SM_YVIRTUALSCREEN = 77
    _SM_CXVIRTUALSCREEN = 78
    _SM_CYVIRTUALSCREEN = 79
    _MAPVK_VSC_TO_VK_EX = 3

    def __init__(self) -> None:
        if os.name != "nt":
            raise BackendUnavailableError("SendInput backend requires Windows")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._configure()
        self.keyboard = KeyboardStateMachine(self._observe_key)
        self._pressed_buttons: set[MouseButton] = set()

    def submit(self, action: PhysicalAction) -> None:
        action.validate()
        if isinstance(action, KeyboardAction):
            self.keyboard.desire(action)
            self._send_keyboard(action.encoding, action.code, action.is_down, action.is_extended)
            self.keyboard.submitted(action)
        elif isinstance(action, UnicodeTextAction):
            self._send_unicode(action.text)
        elif isinstance(action, RelativeMouseAction):
            self._send_mouse(action.dx, action.dy, 0, self._MOUSEEVENTF_MOVE)
        elif isinstance(action, AbsolutePointerAction):
            normalized_x, normalized_y = self._normalize_absolute(action.x, action.y)
            self._send_mouse(
                normalized_x,
                normalized_y,
                0,
                self._MOUSEEVENTF_MOVE | self._MOUSEEVENTF_ABSOLUTE | self._MOUSEEVENTF_VIRTUALDESK,
            )
        elif isinstance(action, MouseButtonAction):
            self._send_button(action.button, action.is_down)
            if action.is_down:
                self._pressed_buttons.add(action.button)
            else:
                self._pressed_buttons.discard(action.button)
        elif isinstance(action, WheelAction):
            self._send_mouse(0, 0, action.delta, self._MOUSEEVENTF_WHEEL)
        elif isinstance(action, (GamepadAction, RawInputAction)):
            raise BackendUnavailableError(f"{type(action).__name__} requires a dedicated backend")
        else:
            raise ContractViolation(f"unsupported physical action: {type(action).__name__}")

    def neutralize(self) -> None:
        self.release_all()

    def release_all(self) -> None:
        snapshot = self.keyboard.snapshot()
        for encoding, code, extended in snapshot.submitted:
            self._send_keyboard(encoding, code, False, extended)
        for button in tuple(self._pressed_buttons):
            self._send_button(button, False)
        self.keyboard.clear()
        self._pressed_buttons.clear()

    def key_is_pressed(
        self,
        encoding: KeyEncoding,
        code: int,
        is_extended: bool = False,
    ) -> bool:
        """Observe the real Win32 key state for supervised neutralization tests."""
        return self._observe_key((encoding, code, is_extended))

    def _send_keyboard(
        self, encoding: KeyEncoding, code: int, is_down: bool, is_extended: bool
    ) -> None:
        flags = 0 if is_down else self._KEYEVENTF_KEYUP
        virtual_key = code
        scan_code = 0
        if encoding == KeyEncoding.SCAN_CODE:
            flags |= self._KEYEVENTF_SCANCODE
            virtual_key = 0
            scan_code = code
        if is_extended:
            flags |= self._KEYEVENTF_EXTENDEDKEY
        item = _INPUT(
            type=self._INPUT_KEYBOARD,
            ki=_KEYBDINPUT(virtual_key, scan_code, flags, 0, 0),
        )
        self._send(item)

    def _send_unicode(self, text: str) -> None:
        encoded = text.encode("utf-16-le")
        for index in range(0, len(encoded), 2):
            code_unit = int.from_bytes(encoded[index : index + 2], "little")
            for is_down in (True, False):
                flags = self._KEYEVENTF_UNICODE
                if not is_down:
                    flags |= self._KEYEVENTF_KEYUP
                self._send(
                    _INPUT(
                        type=self._INPUT_KEYBOARD,
                        ki=_KEYBDINPUT(0, code_unit, flags, 0, 0),
                    )
                )

    def _send_button(self, button: MouseButton, is_down: bool) -> None:
        flags_by_button = {
            MouseButton.LEFT: (self._MOUSEEVENTF_LEFTDOWN, self._MOUSEEVENTF_LEFTUP, 0),
            MouseButton.RIGHT: (self._MOUSEEVENTF_RIGHTDOWN, self._MOUSEEVENTF_RIGHTUP, 0),
            MouseButton.MIDDLE: (self._MOUSEEVENTF_MIDDLEDOWN, self._MOUSEEVENTF_MIDDLEUP, 0),
            MouseButton.X1: (self._MOUSEEVENTF_XDOWN, self._MOUSEEVENTF_XUP, 1),
            MouseButton.X2: (self._MOUSEEVENTF_XDOWN, self._MOUSEEVENTF_XUP, 2),
        }
        down, up, data = flags_by_button[button]
        self._send_mouse(0, 0, data, down if is_down else up)

    def _send_mouse(self, dx: int, dy: int, data: int, flags: int) -> None:
        item = _INPUT(
            type=self._INPUT_MOUSE,
            mi=_MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0),
        )
        self._send(item)

    def _send(self, item: _INPUT) -> None:
        ctypes.set_last_error(0)
        # SendInput consumes an LPINPUT array. Passing a byref CArgObject can
        # update global key state without reliably dispatching window messages
        # on current Windows builds; use the exact array ABI even for one item.
        batch = (_INPUT * 1)(item)
        sent = int(self._user32.SendInput(1, batch, ctypes.sizeof(_INPUT)))
        if sent != 1:
            error = ctypes.get_last_error()
            detail = f"Win32 error {error}" if error else "blocked, possibly by UIPI"
            raise BackendUnavailableError(f"SendInput failed: {detail}")

    def _normalize_absolute(self, x: int, y: int) -> tuple[int, int]:
        left = int(self._user32.GetSystemMetrics(self._SM_XVIRTUALSCREEN))
        top = int(self._user32.GetSystemMetrics(self._SM_YVIRTUALSCREEN))
        width = int(self._user32.GetSystemMetrics(self._SM_CXVIRTUALSCREEN))
        height = int(self._user32.GetSystemMetrics(self._SM_CYVIRTUALSCREEN))
        if width <= 1 or height <= 1:
            raise BackendUnavailableError("virtual desktop dimensions are invalid")
        normalized_x = round((x - left) * 65535 / (width - 1))
        normalized_y = round((y - top) * 65535 / (height - 1))
        return max(0, min(65535, normalized_x)), max(0, min(65535, normalized_y))

    def _observe_key(self, key: KeyId) -> bool:
        encoding, code, _ = key
        virtual_key = (
            int(self._user32.MapVirtualKeyW(code, self._MAPVK_VSC_TO_VK_EX))
            if encoding == KeyEncoding.SCAN_CODE
            else code
        )
        return bool(self._user32.GetAsyncKeyState(virtual_key) & 0x8000)

    def _configure(self) -> None:
        self._user32.SendInput.argtypes = [
            wintypes.UINT,
            ctypes.POINTER(_INPUT),
            ctypes.c_int,
        ]
        self._user32.SendInput.restype = wintypes.UINT
        self._user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        self._user32.GetSystemMetrics.restype = ctypes.c_int
        self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._user32.GetAsyncKeyState.restype = ctypes.c_short
        self._user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        self._user32.MapVirtualKeyW.restype = wintypes.UINT
