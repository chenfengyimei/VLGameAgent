from __future__ import annotations

import uuid

from uga.control.lease import ControlLease, ControlMode, ControlOwner
from uga.control.lifetime import ActionLifetime
from uga.control.physical import (
    AbsolutePointerAction,
    KeyboardAction,
    KeyEncoding,
    MouseButton,
    MouseButtonAction,
    PhysicalAction,
    UnicodeTextAction,
    WheelAction,
)
from uga.control.proposal import ActionProposal
from uga.core.errors import ContractViolation
from uga.gui.schema import GuiAction, GuiActionKind
from uga.time.clock import UGATime
from uga.windows.coordinates import CoordinateSpace, CoordinateTransform, Point


class GuiControlBridge:
    """Explicit normalized-to-physical translation followed by a leased proposal."""

    def translate(
        self, action: GuiAction, transform: CoordinateTransform
    ) -> tuple[PhysicalAction, ...]:
        action.validate()
        if action.kind in (GuiActionKind.WAIT, GuiActionKind.DONE):
            return ()
        if action.kind == GuiActionKind.TYPE:
            return self._type_text(action)
        if action.kind in (GuiActionKind.KEY, GuiActionKind.HOTKEY):
            return self._key_sequence(action)
        assert action.x is not None and action.y is not None
        point = transform.convert(
            Point(action.x, action.y),
            CoordinateSpace.MODEL_NORMALIZED,
            CoordinateSpace.PHYSICAL_SCREEN_PIXEL,
        )
        move = AbsolutePointerAction(
            f"{action.action_id}:move",
            action.lifetime,
            round(point.x),
            round(point.y),
            CoordinateSpace.PHYSICAL_SCREEN_PIXEL,
        )
        if action.kind == GuiActionKind.SCROLL:
            return (
                move,
                WheelAction(f"{action.action_id}:wheel", action.lifetime, action.scroll_delta),
            )
        if action.kind == GuiActionKind.DRAG:
            assert action.end_x is not None and action.end_y is not None
            end = transform.convert(
                Point(action.end_x, action.end_y),
                CoordinateSpace.MODEL_NORMALIZED,
                CoordinateSpace.PHYSICAL_SCREEN_PIXEL,
            )
            # A joystick needs a real held drag, not three instantaneous
            # mouse events.  Keep the pointer down while the scheduler moves
            # it at 450ms and release it at 700ms, within the one-second
            # grounded-action lifetime.
            move_at = UGATime(action.lifetime.effective_from.value_ns + 450_000_000)
            release_at = UGATime(action.lifetime.effective_from.value_ns + 700_000_000)
            move_lifetime = ActionLifetime(
                action.lifetime.created_at, move_at, action.lifetime.expires_at
            )
            release_lifetime = ActionLifetime(
                action.lifetime.created_at, release_at, action.lifetime.expires_at
            )
            return (
                move,
                MouseButtonAction(
                    f"{action.action_id}:down", action.lifetime, MouseButton.LEFT, True
                ),
                AbsolutePointerAction(
                    f"{action.action_id}:drag",
                    move_lifetime,
                    round(end.x),
                    round(end.y),
                    CoordinateSpace.PHYSICAL_SCREEN_PIXEL,
                ),
                MouseButtonAction(
                    f"{action.action_id}:up", release_lifetime, MouseButton.LEFT, False
                ),
            )
        if action.kind == GuiActionKind.LONG_CLICK:
            release_at = UGATime(
                action.lifetime.effective_from.value_ns + 700_000_000
            )
            release_lifetime = ActionLifetime(
                action.lifetime.created_at,
                release_at,
                action.lifetime.expires_at,
            )
            return (
                move,
                MouseButtonAction(
                    f"{action.action_id}:down", action.lifetime, MouseButton.LEFT, True
                ),
                MouseButtonAction(
                    f"{action.action_id}:up", release_lifetime, MouseButton.LEFT, False
                ),
            )
        button = MouseButton.RIGHT if action.kind == GuiActionKind.RIGHT_CLICK else MouseButton.LEFT
        clicks = 2 if action.kind == GuiActionKind.DOUBLE_CLICK else 1
        result: list[PhysicalAction] = [move]
        for click in range(clicks):
            result.extend(
                (
                    MouseButtonAction(
                        f"{action.action_id}:down:{click}", action.lifetime, button, True
                    ),
                    MouseButtonAction(
                        f"{action.action_id}:up:{click}", action.lifetime, button, False
                    ),
                )
            )
        return tuple(result)

    def proposal(
        self,
        actions: tuple[GuiAction, ...],
        transform: CoordinateTransform,
        lease: ControlLease,
        *,
        producer: str,
        observation_id: str,
    ) -> ActionProposal | None:
        if lease.owner != ControlOwner.GUI_AGENT or lease.mode != ControlMode.GUI:
            raise ContractViolation("GUI control requires a GUI_AGENT lease in GUI mode")
        physical = tuple(item for action in actions for item in self.translate(action, transform))
        if not physical:
            return None
        created = min(action.lifetime.created_at for action in actions)
        effective = min(action.lifetime.effective_from for action in actions)
        expires = max(action.lifetime.expires_at for action in actions)
        lifetime = ActionLifetime(created, effective, expires)
        return ActionProposal(
            uuid.uuid4().hex,
            producer,
            lease.owner,
            lease.mode,
            lease.lease_id,
            lease.generation,
            lifetime,
            physical,
            observation_id,
            min(action.confidence for action in actions),
        )

    @staticmethod
    def _key_sequence(action: GuiAction) -> tuple[PhysicalAction, ...]:
        down = tuple(
            KeyboardAction(
                f"{action.action_id}:key:{code}:down",
                action.lifetime,
                code,
                True,
                KeyEncoding.VIRTUAL_KEY,
            )
            for code in action.key_codes
        )
        up = tuple(
            KeyboardAction(
                f"{action.action_id}:key:{code}:up",
                action.lifetime,
                code,
                False,
                KeyEncoding.VIRTUAL_KEY,
            )
            for code in reversed(action.key_codes)
        )
        return down + up

    @staticmethod
    def _type_text(action: GuiAction) -> tuple[PhysicalAction, ...]:
        assert action.text is not None
        return (UnicodeTextAction(f"{action.action_id}:text", action.lifetime, action.text),)
