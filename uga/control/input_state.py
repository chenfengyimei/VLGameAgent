from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

from uga.control.physical import KeyboardAction, KeyEncoding

KeyId = tuple[KeyEncoding, int, bool]


@dataclass(frozen=True, slots=True)
class KeyboardStateSnapshot:
    desired: frozenset[KeyId]
    submitted: frozenset[KeyId]
    observed: frozenset[KeyId]


class KeyboardStateMachine:
    """Tracks requested, emitted, and optionally observed keyboard state."""

    def __init__(self, observer: Callable[[KeyId], bool] | None = None) -> None:
        self._desired: set[KeyId] = set()
        self._submitted: set[KeyId] = set()
        self._observer = observer
        self._lock = Lock()

    def desire(self, action: KeyboardAction) -> None:
        key = (action.encoding, action.code, action.is_extended)
        with self._lock:
            if action.is_down:
                self._desired.add(key)
            else:
                self._desired.discard(key)

    def submitted(self, action: KeyboardAction) -> None:
        key = (action.encoding, action.code, action.is_extended)
        with self._lock:
            if action.is_down:
                self._submitted.add(key)
            else:
                self._submitted.discard(key)

    def snapshot(self) -> KeyboardStateSnapshot:
        with self._lock:
            desired = frozenset(self._desired)
            submitted = frozenset(self._submitted)
        observed = (
            frozenset(key for key in desired | submitted if self._observer(key))
            if self._observer is not None
            else frozenset()
        )
        return KeyboardStateSnapshot(desired, submitted, observed)

    def clear(self) -> None:
        with self._lock:
            self._desired.clear()
            self._submitted.clear()
