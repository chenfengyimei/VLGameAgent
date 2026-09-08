from __future__ import annotations

from typing import Protocol, runtime_checkable

from uga.control.input_backend import InputBackend
from uga.control.physical import GamepadAction, PhysicalAction
from uga.core.errors import BackendUnavailableError


@runtime_checkable
class VirtualGamepadDriver(Protocol):
    """Optional adapter implemented by an installed virtual-controller provider."""

    def update(
        self,
        *,
        left_x: float,
        left_y: float,
        right_x: float,
        right_y: float,
        left_trigger: float,
        right_trigger: float,
        buttons: int,
    ) -> None: ...

    def neutral(self) -> None: ...


class OptionalGamepadBackend:
    """Strict gamepad backend that is available only with an explicit driver."""

    def __init__(self, driver: VirtualGamepadDriver | None = None) -> None:
        self._driver = driver

    @property
    def available(self) -> bool:
        return self._driver is not None

    def submit(self, action: PhysicalAction) -> None:
        if not isinstance(action, GamepadAction):
            raise BackendUnavailableError("optional gamepad backend accepts only gamepad actions")
        action.validate()
        if self._driver is None:
            raise BackendUnavailableError("no virtual gamepad driver is installed")
        self._driver.update(
            left_x=action.left_x,
            left_y=action.left_y,
            right_x=action.right_x,
            right_y=action.right_y,
            left_trigger=action.left_trigger,
            right_trigger=action.right_trigger,
            buttons=action.buttons,
        )

    def neutralize(self) -> None:
        self.release_all()

    def release_all(self) -> None:
        if self._driver is not None:
            self._driver.neutral()


class RoutedInputBackend:
    """Routes gamepad actions separately while preserving one executor boundary."""

    def __init__(self, pointer_keyboard: InputBackend, gamepad: InputBackend) -> None:
        self._pointer_keyboard = pointer_keyboard
        self._gamepad = gamepad

    def submit(self, action: PhysicalAction) -> None:
        if isinstance(action, GamepadAction):
            self._gamepad.submit(action)
        else:
            self._pointer_keyboard.submit(action)

    def neutralize(self) -> None:
        self._pointer_keyboard.neutralize()
        self._gamepad.neutralize()

    def release_all(self) -> None:
        errors: list[Exception] = []
        for backend in (self._pointer_keyboard, self._gamepad):
            try:
                backend.release_all()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise BackendUnavailableError(
                "one or more input backends failed to release: "
                + "; ".join(str(error) for error in errors)
            )
