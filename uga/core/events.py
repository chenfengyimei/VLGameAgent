from __future__ import annotations

import asyncio
import inspect
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.time.clock import ClockBackend, UGATime


class EventType(StrEnum):
    RUNTIME_STARTED = "runtime_started"
    RUNTIME_PAUSED = "runtime_paused"
    RUNTIME_RESUMED = "runtime_resumed"
    RUNTIME_STOPPED = "runtime_stopped"
    RUNTIME_SHUTDOWN = "runtime_shutdown"
    FRAME_CAPTURED = "frame_captured"
    CAPTURE_BACKEND_SELECTED = "capture_backend_selected"
    CAPTURE_BACKEND_FAILED = "capture_backend_failed"
    FRAME_DROPPED = "frame_dropped"
    OBSERVATION_BUILT = "observation_built"
    GOAL_CHANGED = "goal_changed"
    TASK_ACTIVATED = "task_activated"
    MODE_CANDIDATE = "mode_candidate"
    MODE_CHANGED = "mode_changed"
    LEASE_GRANTED = "lease_granted"
    LEASE_REVOKED = "lease_revoked"
    SKILL_STARTED = "skill_started"
    SKILL_COMPLETED = "skill_completed"
    SKILL_FAILED = "skill_failed"
    POLICY_INFERENCE_STARTED = "policy_inference_started"
    POLICY_INFERENCE_COMPLETED = "policy_inference_completed"
    REASONING_REQUESTED = "reasoning_requested"
    ACTION_PROPOSED = "action_proposed"
    ACTION_ACCEPTED = "action_accepted"
    ACTION_REJECTED = "action_rejected"
    ACTION_EXPIRED = "action_expired"
    ACTION_EXECUTED = "action_executed"
    PROGRESS_DETECTED = "progress_detected"
    AGENT_STUCK = "agent_stuck"
    HUMAN_OVERRIDE = "human_override"
    EMERGENCY_STOP = "emergency_stop"


@dataclass(frozen=True, slots=True)
class Event(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.event"

    event_type: EventType
    timestamp: UGATime
    source: str
    payload: dict[str, object]

    def validate(self) -> None:
        self.timestamp.validate()
        if not self.source.strip():
            raise ContractViolation("event source cannot be empty")


EventHandler = Callable[[Event], Awaitable[None] | None]


class EventBus:
    """Small in-process event bus with bounded diagnostic history."""

    def __init__(self, clock: ClockBackend, history_size: int = 256) -> None:
        if history_size < 1:
            raise ContractViolation("event history size must be positive")
        self._clock = clock
        self._handlers: dict[EventType | None, list[EventHandler]] = {}
        self._history: deque[Event] = deque(maxlen=history_size)
        self._lock = asyncio.Lock()

    def subscribe(self, handler: EventHandler, event_type: EventType | None = None) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    async def publish(
        self,
        event_type: EventType,
        source: str,
        payload: dict[str, object] | None = None,
    ) -> Event:
        event = Event(event_type, self._clock.now(), source, payload or {})
        event.validate()
        async with self._lock:
            self._history.append(event)
            handlers = tuple(self._handlers.get(event_type, ())) + tuple(
                self._handlers.get(None, ())
            )
        for handler in handlers:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        return event

    def history(self) -> tuple[Event, ...]:
        return tuple(self._history)
