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
    ) -> None:
        if not taps:
            raise ValueError("scripted tap policy requires at least one tap")
        self._taps = sorted(taps)
        self._policy_version = policy_version
        self._started = time.monotonic()
        self._index = 0

    @property
    def policy_version(self) -> str:
        return self._policy_version

    def infer(self, context: PolicyContext) -> FastPolicyOutput:
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

    def _idle_chunk(self, context: PolicyContext, duration: float) -> FastPolicyOutput:
        ticks = max(1, int(duration * 30.0))
        hold_index = min(self._index, len(self._taps) - 1)
        hold_x = float(self._taps[hold_index][1])
        hold_y = float(self._taps[hold_index][2])
        chunk = ActionChunk(
            chunk_id=f"scripted-idle-{uuid.uuid4().hex[:8]}",
            observation_id=context.observation_id,
            generated_at=context.generated_at,
            effective_from=context.generated_at,
            expires_at=UGATime(context.generated_at.value_ns + int(duration * 1_000_000_000)),
            tick_rate_hz=30.0,
            move_x=(0.0,) * ticks,
            move_y=(0.0,) * ticks,
            look_x=(0.0,) * ticks,
            look_y=(0.0,) * ticks,
            buttons=(0,) * ticks,
            confidence=1.0,
            policy_version=self._policy_version,
            pointer_x=hold_x,
            pointer_y=hold_y,
        )
        return FastPolicyOutput(chunk, False, None, 30.0, 30.0)
