"""Scripted tap policy: executes a fixed tap timeline through the agent loop.

A V1-scope Fast Policy for pointer-driven games (Android emulators): the task
is a timeline of ``(delay_seconds, x, y)`` taps in physical screen pixels. The
policy emits one one-tick INTERACT chunk carrying the tap's pointer position
when the scheduled time arrives, and hands control back (need_reasoning) once
the timeline is exhausted.
"""

from __future__ import annotations

import time
import uuid

from uga.policy.action_chunk import ActionButton, ActionChunk
from uga.policy.fast_policy import FastPolicyOutput, PolicyContext
from uga.time.clock import UGATime


class ScriptedTapPolicy:
    """Timeline-driven tap policy for pointer-driven targets."""

    def __init__(
        self,
        taps: list[tuple[float, int, int]],
        *,
        policy_version: str = "scripted-tap-v1",
        repeat_interval_s: float | None = None,
    ) -> None:
        if not taps:
            raise ValueError("scripted tap policy requires at least one tap")
        if repeat_interval_s is not None:
            if repeat_interval_s <= 0.0:
                raise ValueError("scripted tap repeat interval must be positive")
            if any(delay >= repeat_interval_s for delay, _, _ in taps):
                raise ValueError("scripted tap repeat interval must exceed every tap delay")
        self._taps = sorted(taps)
        self._repeat = repeat_interval_s
        self._policy_version = policy_version
        self._started = time.monotonic()
        self._index = 0

    @property
    def policy_version(self) -> str:
        return self._policy_version

    def infer(self, context: PolicyContext) -> FastPolicyOutput:
        if self._repeat is not None:
            self._skip_stale_taps()
            if self._index >= len(self._taps) and time.monotonic() - self._started >= self._repeat:
                # The previous cycle is exhausted and the next one has begun.
                self._started += self._repeat
                self._index = 0
                self._skip_stale_taps()
        elapsed = time.monotonic() - self._started
        if self._index >= len(self._taps):
            return self._idle_chunk(context, duration=0.5)
        due_at, tap_x, tap_y = self._taps[self._index]
        remaining = due_at - elapsed
        if remaining > 0.05:
            return self._idle_chunk(context, duration=remaining)
        # Fire as soon as the tap is due; a late tap still beats a silently
        # dropped one when observation cadence skips over the trigger instant.
        self._index += 1
        chunk = ActionChunk(
            chunk_id=f"scripted-tap-{self._index}-{uuid.uuid4().hex[:8]}",
            observation_id=context.observation_id,
            generated_at=context.generated_at,
            effective_from=context.generated_at,
            expires_at=UGATime(context.generated_at.value_ns + 1_000_000_000),
            tick_rate_hz=1.0,
            move_x=(0.0,),
            move_y=(0.0,),
            look_x=(0.0,),
            look_y=(0.0,),
            buttons=(int(ActionButton.INTERACT),),
            confidence=1.0,
            policy_version=self._policy_version,
            pointer_x=float(tap_x),
            pointer_y=float(tap_y),
        )
        return FastPolicyOutput(chunk, False, None, 30.0, 30.0)

    def _pending_due(self) -> float:
        """Absolute monotonic time of the next tap that has not fired yet."""
        assert self._repeat is not None
        if self._index < len(self._taps):
            return self._started + self._taps[self._index][0]
        return self._started + self._repeat + self._taps[0][0]

    def _skip_stale_taps(self) -> None:
        # A tap overdue by at least one whole interval belongs to a cycle the
        # agent never saw; dropping it prevents burst-firing stale taps after
        # a long capture stall. The newest overdue tap still fires once.
        repeat = self._repeat
        assert repeat is not None
        now = time.monotonic()
        while now - self._pending_due() >= repeat:
            if self._index < len(self._taps) - 1:
                self._index += 1
            else:
                self._started += repeat
                self._index = 0

    def _idle_chunk(self, context: PolicyContext, duration: float) -> FastPolicyOutput:
        # An absolute pointer position is stateless: one move is enough to
        # establish the idle location.  Repeating the same move at 30 Hz only
        # inflates the scheduler/episode and each observation replaces the
        # previous lease before most of those duplicate actions can execute.
        hold_index = min(self._index, len(self._taps) - 1)
        hold_x = float(self._taps[hold_index][1])
        hold_y = float(self._taps[hold_index][2])
        chunk = ActionChunk(
            chunk_id=f"scripted-idle-{uuid.uuid4().hex[:8]}",
            observation_id=context.observation_id,
            generated_at=context.generated_at,
            effective_from=context.generated_at,
            expires_at=UGATime(context.generated_at.value_ns + int(duration * 1_000_000_000)),
            tick_rate_hz=1.0,
            move_x=(0.0,),
            move_y=(0.0,),
            look_x=(0.0,),
            look_y=(0.0,),
            buttons=(0,),
            confidence=1.0,
            policy_version=self._policy_version,
            pointer_x=hold_x,
            pointer_y=hold_y,
        )
        return FastPolicyOutput(chunk, False, None, 30.0, 30.0)
