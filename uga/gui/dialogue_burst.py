from __future__ import annotations

import uuid
from dataclasses import dataclass

from uga.capture.frame import Frame
from uga.core.errors import ContractViolation
from uga.time.clock import UGATime


@dataclass(frozen=True, slots=True)
class DialogueBurstPermit:
    """One short-lived click authorization backed by a recent OCR anchor."""

    token: str
    x: float
    y: float
    validated_frame: Frame
    task_generation: int


@dataclass(slots=True)
class _DialogueBurstState:
    token: str
    x: float
    y: float
    validated_frame: Frame
    task_generation: int
    confirmed_until_ns: int
    next_click_ns: int
    remaining_clicks: int


class DialogueBurstGate:
    """Turns one OCR dialogue proof into a bounded series of GUI clicks.

    The gate deliberately does not run OCR.  The ordinary observation loop
    refreshes or revokes its authority.  Limiting every confirmation to a
    small click budget prevents a stale dialogue observation from becoming an
    unbounded blind clicker if perception stalls.
    """

    def __init__(
        self,
        *,
        interval_ns: int = 250_000_000,
        confirmation_ttl_ns: int = 1_250_000_000,
        clicks_per_confirmation: int = 3,
    ) -> None:
        if interval_ns <= 0 or confirmation_ttl_ns < interval_ns:
            raise ContractViolation("dialogue burst timing must be positive and ordered")
        if type(clicks_per_confirmation) is not int or clicks_per_confirmation < 1:
            raise ContractViolation("dialogue burst click budget must be positive")
        self._interval_ns = interval_ns
        self._confirmation_ttl_ns = confirmation_ttl_ns
        self._clicks_per_confirmation = clicks_per_confirmation
        self._state: _DialogueBurstState | None = None

    @property
    def active(self) -> bool:
        return self._state is not None

    @property
    def interval_ns(self) -> int:
        return self._interval_ns

    def arm(
        self,
        *,
        x: float,
        y: float,
        validated_frame: Frame,
        task_generation: int,
        now: UGATime,
    ) -> None:
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            raise ContractViolation("dialogue burst hotspot must be normalized")
        self._state = _DialogueBurstState(
            uuid.uuid4().hex,
            x,
            y,
            validated_frame,
            task_generation,
            now.value_ns + self._confirmation_ttl_ns,
            now.value_ns + self._interval_ns,
            self._clicks_per_confirmation,
        )

    def refresh(
        self,
        *,
        dialogue_active: bool,
        validated_frame: Frame,
        task_generation: int,
        now: UGATime,
    ) -> None:
        state = self._state
        if state is None:
            return
        if not dialogue_active or not _same_window_geometry(
            state.validated_frame, validated_frame
        ):
            self.disarm()
            return
        state.validated_frame = validated_frame
        state.task_generation = task_generation
        state.confirmed_until_ns = now.value_ns + self._confirmation_ttl_ns
        state.remaining_clicks = self._clicks_per_confirmation

    def take_due(self, now: UGATime) -> DialogueBurstPermit | None:
        state = self._state
        if state is None:
            return None
        if now.value_ns > state.confirmed_until_ns:
            self.disarm()
            return None
        if state.remaining_clicks <= 0 or now.value_ns < state.next_click_ns:
            return None
        state.remaining_clicks -= 1
        # Never catch up with a rapid click storm after the event loop was
        # delayed: each permit starts a fresh interval from the current time.
        state.next_click_ns = now.value_ns + self._interval_ns
        return DialogueBurstPermit(
            state.token,
            state.x,
            state.y,
            state.validated_frame,
            state.task_generation,
        )

    def permit_is_current(self, permit: DialogueBurstPermit, now: UGATime) -> bool:
        state = self._state
        return bool(
            state is not None
            and permit.token == state.token
            and now.value_ns <= state.confirmed_until_ns
            and state.task_generation == permit.task_generation
        )

    def disarm(self) -> None:
        self._state = None


def _same_window_geometry(left: Frame, right: Frame) -> bool:
    return (
        left.window_identity == right.window_identity
        and left.width == right.width
        and left.height == right.height
        and left.client_rect == right.client_rect
        and left.physical_rect == right.physical_rect
    )
