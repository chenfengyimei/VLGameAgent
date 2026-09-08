from __future__ import annotations

from typing import Protocol, runtime_checkable

from uga.dashboard.state import DashboardCommand


@runtime_checkable
class OperatorControl(Protocol):
    def start(self) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def stop(self) -> None: ...

    def take_control(self) -> None: ...

    def release_control(self) -> None: ...

    def emergency_release(self) -> None: ...


class DashboardCommandRouter:
    """Explicit operator-command boundary; dashboard HTML executes nothing directly."""

    def __init__(self, control: OperatorControl) -> None:
        self._control = control

    def execute(self, command: DashboardCommand) -> None:
        handlers = {
            DashboardCommand.START: self._control.start,
            DashboardCommand.PAUSE: self._control.pause,
            DashboardCommand.RESUME: self._control.resume,
            DashboardCommand.STOP: self._control.stop,
            DashboardCommand.TAKE_CONTROL: self._control.take_control,
            DashboardCommand.RELEASE_CONTROL: self._control.release_control,
            DashboardCommand.EMERGENCY_RELEASE: self._control.emergency_release,
        }
        handlers[command]()
