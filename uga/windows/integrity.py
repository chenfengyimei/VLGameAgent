from __future__ import annotations

import ctypes
import os
from enum import IntEnum
from typing import Protocol, runtime_checkable

from uga.core.errors import BackendUnavailableError


class IntegrityLevel(IntEnum):
    UNKNOWN = -1
    UNTRUSTED = 0x0000
    LOW = 0x1000
    MEDIUM = 0x2000
    HIGH = 0x3000
    SYSTEM = 0x4000
    PROTECTED = 0x5000


@runtime_checkable
class IntegrityProvider(Protocol):
    def current_process(self) -> IntegrityLevel: ...

    def process(self, pid: int) -> IntegrityLevel: ...


if os.name == "nt":
    from ctypes import wintypes

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
        _fields_ = [("Label", _SID_AND_ATTRIBUTES)]


class Win32IntegrityProvider:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TOKEN_INTEGRITY_LEVEL = 25
    ERROR_INSUFFICIENT_BUFFER = 122

    def __init__(self) -> None:
        if os.name != "nt":
            raise BackendUnavailableError("integrity levels require Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._configure()

    def current_process(self) -> IntegrityLevel:
        return self.process(os.getpid())

    def process(self, pid: int) -> IntegrityLevel:
        process = self._kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            return IntegrityLevel.UNKNOWN
        token = ctypes.c_void_p()
        try:
            if not self._advapi32.OpenProcessToken(process, self.TOKEN_QUERY, ctypes.byref(token)):
                return IntegrityLevel.UNKNOWN
            size = wintypes.DWORD()
            self._advapi32.GetTokenInformation(
                token, self.TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size)
            )
            if ctypes.get_last_error() != self.ERROR_INSUFFICIENT_BUFFER or size.value == 0:
                return IntegrityLevel.UNKNOWN
            buffer = ctypes.create_string_buffer(size.value)
            if not self._advapi32.GetTokenInformation(
                token,
                self.TOKEN_INTEGRITY_LEVEL,
                buffer,
                size,
                ctypes.byref(size),
            ):
                return IntegrityLevel.UNKNOWN
            label = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_MANDATORY_LABEL)).contents
            count_pointer = self._advapi32.GetSidSubAuthorityCount(label.Label.Sid)
            if not count_pointer:
                return IntegrityLevel.UNKNOWN
            count = int(count_pointer.contents.value)
            rid_pointer = self._advapi32.GetSidSubAuthority(label.Label.Sid, count - 1)
            if not rid_pointer:
                return IntegrityLevel.UNKNOWN
            rid = int(rid_pointer.contents.value)
            return _level_from_rid(rid)
        finally:
            if token.value:
                self._kernel32.CloseHandle(token)
            self._kernel32.CloseHandle(process)

    def _configure(self) -> None:
        self._kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenProcess.restype = ctypes.c_void_p
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._advapi32.OpenProcessToken.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._advapi32.OpenProcessToken.restype = wintypes.BOOL
        self._advapi32.GetTokenInformation.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._advapi32.GetTokenInformation.restype = wintypes.BOOL
        self._advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
        self._advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        self._advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        self._advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)


def _level_from_rid(rid: int) -> IntegrityLevel:
    known = sorted(
        (level for level in IntegrityLevel if level is not IntegrityLevel.UNKNOWN), reverse=True
    )
    return next((level for level in known if rid >= level.value), IntegrityLevel.UNKNOWN)
