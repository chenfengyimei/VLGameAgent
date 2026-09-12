from __future__ import annotations

from dataclasses import replace

from uga.control.canonical import CanonicalAction
from uga.control.physical import (
    AbsolutePointerAction,
    KeyboardAction,
    KeyEncoding,
    MouseButton,
    MouseButtonAction,
    PhysicalAction,
    RelativeMouseAction,
)
from uga.core.errors import ContractViolation
from uga.environment.profile import BindingKind, ControlBinding, GameProfile
from uga.observation.schema import Observation
from uga.windows.coordinates import CoordinateSpace


class GenericEnvironment:
    """Profile-driven fallback that never explores unknown controls by pressing keys."""

    def __init__(self, profile: GameProfile) -> None:
        profile.validate()
        self._profile = profile

    @property
    def profile(self) -> GameProfile:
        return self._profile

    def enrich_observation(self, observation: Observation) -> Observation:
        observation.validate()
        if observation.belief_state.game_id != self._profile.game_id:
            raise ContractViolation("observation game does not match environment profile")
        return replace(observation, visible_text=tuple(observation.visible_text))

    def adapt_action(self, action: CanonicalAction) -> tuple[PhysicalAction, ...]:
        action.validate()
        result: list[PhysicalAction] = []
        held = {
            "move_forward": action.move_y > 0.1,
            "move_backward": action.move_y < -0.1,
            "move_right": action.move_x > 0.1,
            "move_left": action.move_x < -0.1,
            "sprint": action.sprint,
            "crouch": action.crouch,
        }
        for name, is_down in held.items():
            binding = self._profile.binding(name)
            if binding is None:
                continue
            result.append(self._key_action(action, binding, is_down))
        pulses = {
            "jump": action.jump,
            "interact": action.interact,
            "primary": action.primary,
            "secondary": action.secondary,
            "menu": action.menu,
            "confirm": action.confirm,
            "back": action.back,
        }
        if action.pointer_x is not None and action.pointer_y is not None:
            if self._profile.camera_type != "absolute_pointer":
                raise ContractViolation(
                    "canonical pointer coordinates require an absolute-pointer camera profile"
                )
            result.append(
                AbsolutePointerAction(
                    f"{action.action_id}:pointer",
                    action.lifetime,
                    round(action.pointer_x),
                    round(action.pointer_y),
                    CoordinateSpace.PHYSICAL_SCREEN_PIXEL,
                )
            )
            if action.pointer_down or action.pointer_up:
                binding = self._profile.binding("interact")
                if binding is None or binding.kind is not BindingKind.MOUSE_BUTTON:
                    raise ContractViolation(
                        "pointer press/release requires a confirmed mouse_button interact binding"
                    )
                if action.pointer_down:
                    result.append(self._mouse_button_action(action, binding, True))
                if action.pointer_up:
                    result.append(self._mouse_button_action(action, binding, False))
        for name, active in pulses.items():
            binding = self._profile.binding(name)
            if binding is None or not active:
                continue
            if binding.kind is BindingKind.MOUSE_BUTTON:
                if action.pointer_x is None or action.pointer_y is None:
                    raise ContractViolation(
                        f"pointer binding {binding.action!r} requires canonical"
                        " pointer coordinates"
                    )
                result.extend(
                    (
                        self._mouse_button_action(action, binding, True),
                        self._mouse_button_action(action, binding, False),
                    )
                )
                continue
            result.extend(
                (
                    self._key_action(action, binding, True, suffix="pulse-down"),
                    self._key_action(action, binding, False, suffix="pulse-up"),
                )
            )
        if action.look_x or action.look_y:
            if self._profile.camera_type != "relative_mouse":
                raise ContractViolation("generic look axes require a relative-mouse camera profile")
            sensitivity = self._profile.camera_sensitivity
            result.append(
                RelativeMouseAction(
                    f"{action.action_id}:look",
                    action.lifetime,
                    round(action.look_x * sensitivity),
                    round(action.look_y * sensitivity),
                )
            )
        return tuple(result)

    def _key_action(
        self,
        action: CanonicalAction,
        binding: ControlBinding,
        is_down: bool,
        *,
        suffix: str | None = None,
    ) -> KeyboardAction:
        self._require_confirmed(binding)
        if binding.kind not in (BindingKind.SCAN_CODE, BindingKind.VIRTUAL_KEY):
            raise ContractViolation(f"generic keyboard adapter cannot emit {binding.kind}")
        if not isinstance(binding.code, int):
            raise ContractViolation(f"keyboard binding {binding.action} must use an integer code")
        event = suffix or ("down" if is_down else "up")
        return KeyboardAction(
            f"{action.action_id}:{binding.action}:{event}",
            action.lifetime,
            binding.code,
            is_down,
            (
                KeyEncoding.SCAN_CODE
                if binding.kind == BindingKind.SCAN_CODE
                else KeyEncoding.VIRTUAL_KEY
            ),
        )

    @staticmethod
    def _require_confirmed(binding: ControlBinding) -> None:
        if not binding.confirmed:
            raise ContractViolation(
                f"binding {binding.action!r} must be user-confirmed before input adaptation"
            )

    @staticmethod
    def _mouse_button_action(
        action: CanonicalAction,
        binding: ControlBinding,
        is_down: bool,
    ) -> MouseButtonAction:
        GenericEnvironment._require_confirmed(binding)
        if binding.code != "left":
            raise ContractViolation(
                f"pointer binding {binding.action!r} only supports the left button"
            )
        event = "down" if is_down else "up"
        return MouseButtonAction(
            f"{action.action_id}:{binding.action}:{event}",
            action.lifetime,
            MouseButton.LEFT,
            is_down,
        )
