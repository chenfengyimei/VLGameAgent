from __future__ import annotations

from typing import Protocol, runtime_checkable

from uga.control.physical import PhysicalAction


@runtime_checkable
class InputBackend(Protocol):
    """Physical input boundary available only to InputExecutor."""

    def submit(self, action: PhysicalAction) -> None: ...

    def neutralize(self) -> None: ...

    def release_all(self) -> None: ...


class DryRunInputBackend:
    """Non-mutating backend used by CI and replay validation."""

    def __init__(self) -> None:
        self.actions: list[PhysicalAction] = []
        self.release_count = 0

    def submit(self, action: PhysicalAction) -> None:
        action.validate()
        self.actions.append(action)

    def neutralize(self) -> None:
        self.release_all()

    def release_all(self) -> None:
        self.release_count += 1
