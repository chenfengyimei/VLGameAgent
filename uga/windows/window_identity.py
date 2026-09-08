from __future__ import annotations

import hashlib
import ntpath
from dataclasses import dataclass
from threading import Lock
from typing import ClassVar

from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin


def executable_path_hash(path: str) -> str:
    """Hash a normalized Windows executable path without persisting the path."""
    normalized = ntpath.normcase(ntpath.normpath(path.strip()))
    if not normalized:
        raise ContractViolation("executable path cannot be empty")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class WindowIdentity(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.window_identity"

    hwnd: int
    pid: int
    executable_path_hash: str
    process_start_time_100ns: int
    window_generation: int

    def validate(self) -> None:
        if self.hwnd <= 0:
            raise ContractViolation("hwnd must be positive")
        if self.pid <= 0:
            raise ContractViolation("pid must be positive")
        if len(self.executable_path_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.executable_path_hash
        ):
            raise ContractViolation("executable_path_hash must be lowercase SHA-256 hex")
        if self.process_start_time_100ns < 0:
            raise ContractViolation("process start time cannot be negative")
        if self.window_generation < 1:
            raise ContractViolation("window generation must be positive")

    def __post_init__(self) -> None:
        self.validate()


class WindowIdentityTracker:
    """Assigns generations so a recycled HWND never aliases an old target."""

    def __init__(self) -> None:
        self._signatures: dict[int, tuple[int, str, int]] = {}
        self._generations: dict[int, int] = {}
        self._lock = Lock()

    def identify(
        self,
        *,
        hwnd: int,
        pid: int,
        executable_path: str,
        process_start_time_100ns: int,
    ) -> WindowIdentity:
        path_hash = executable_path_hash(executable_path)
        signature = (pid, path_hash, process_start_time_100ns)
        with self._lock:
            if self._signatures.get(hwnd) != signature:
                self._generations[hwnd] = self._generations.get(hwnd, 0) + 1
                self._signatures[hwnd] = signature
            generation = self._generations[hwnd]
        return WindowIdentity(hwnd, pid, path_hash, process_start_time_100ns, generation)

    def forget(self, hwnd: int) -> None:
        with self._lock:
            self._signatures.pop(hwnd, None)
