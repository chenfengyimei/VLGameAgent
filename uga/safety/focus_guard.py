from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

from uga.control.lease import ControlLease
from uga.control.lease_manager import ControlLeaseManager
from uga.windows.backend import WindowBackend
from uga.windows.integrity import IntegrityLevel, IntegrityProvider
from uga.windows.window_identity import WindowIdentity


class GuardReason(StrEnum):
    ALLOWED = "allowed"
    AGENT_DISABLED = "agent_disabled"
    TARGET_MISSING = "target_missing"
    TARGET_CHANGED = "target_changed"
    TARGET_NOT_FOREGROUND = "target_not_foreground"
    INTEGRITY_UNKNOWN = "integrity_unknown"
    INTEGRITY_INCOMPATIBLE = "integrity_incompatible"
    LEASE_INVALID = "lease_invalid"


@dataclass(frozen=True, slots=True)
class GuardDecision:
    allowed: bool
    reason: GuardReason


class AgentEnableState:
    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled
        self._lock = Lock()

    def set(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = enabled

    def get(self) -> bool:
        with self._lock:
            return self._enabled


class FocusGuard:
    """Fail-closed validation performed immediately before every input write."""

    def __init__(
        self,
        windows: WindowBackend,
        integrity: IntegrityProvider,
        leases: ControlLeaseManager,
        enabled: AgentEnableState,
        *,
        restore_foreground: bool = False,
    ) -> None:
        self._windows = windows
        self._integrity = integrity
        self._leases = leases
        self._enabled = enabled
        self._restore_foreground = restore_foreground

    def check(self, target: WindowIdentity, lease: ControlLease) -> GuardDecision:
        if not self._enabled.get():
            return GuardDecision(False, GuardReason.AGENT_DISABLED)
        try:
            snapshot = self._windows.snapshot(target.hwnd)
        except Exception:
            return GuardDecision(False, GuardReason.TARGET_MISSING)
        if snapshot.identity != target:
            return GuardDecision(False, GuardReason.TARGET_CHANGED)
        if self._windows.foreground_hwnd() != target.hwnd:
            request_foreground = getattr(self._windows, "request_foreground", None)
            if not self._restore_foreground or not callable(request_foreground):
                return GuardDecision(False, GuardReason.TARGET_NOT_FOREGROUND)
            try:
                restored = bool(request_foreground(target.hwnd))
                # Re-read the identity after the focus transition. A recycled
                # HWND must never inherit input intended for the old window.
                refreshed = self._windows.snapshot(target.hwnd)
            except Exception:
                return GuardDecision(False, GuardReason.TARGET_NOT_FOREGROUND)
            if (
                not restored
                or self._windows.foreground_hwnd() != target.hwnd
                or refreshed.identity != target
            ):
                return GuardDecision(False, GuardReason.TARGET_NOT_FOREGROUND)
        current_level = self._integrity.current_process()
        target_level = self._integrity.process(target.pid)
        if current_level == IntegrityLevel.UNKNOWN or target_level == IntegrityLevel.UNKNOWN:
            return GuardDecision(False, GuardReason.INTEGRITY_UNKNOWN)
        if target_level > current_level:
            return GuardDecision(False, GuardReason.INTEGRITY_INCOMPATIBLE)
        if not self._leases.validate(lease):
            return GuardDecision(False, GuardReason.LEASE_INVALID)
        return GuardDecision(True, GuardReason.ALLOWED)
