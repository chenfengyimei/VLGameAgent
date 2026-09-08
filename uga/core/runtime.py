from __future__ import annotations

import asyncio

from uga.core.events import EventBus, EventType
from uga.core.lifecycle import LifecycleState, validate_transition
from uga.time.clock import ClockBackend, PerfCounterClock


class AgentRuntime:
    """Lifecycle shell; safety-wrapped input components are composed externally."""

    def __init__(self, clock: ClockBackend | None = None) -> None:
        self.clock = clock or PerfCounterClock()
        self.events = EventBus(self.clock)
        self._state = LifecycleState.CREATED
        self._lock = asyncio.Lock()

    @property
    def state(self) -> LifecycleState:
        return self._state

    async def _transition(self, target: LifecycleState, event: EventType) -> None:
        async with self._lock:
            validate_transition(self._state, target)
            previous = self._state
            self._state = target
        await self.events.publish(
            event,
            "runtime",
            {"previous_state": previous.value, "state": target.value},
        )

    async def start(self) -> None:
        await self._transition(LifecycleState.RUNNING, EventType.RUNTIME_STARTED)

    async def pause(self) -> None:
        await self._transition(LifecycleState.PAUSED, EventType.RUNTIME_PAUSED)

    async def resume(self) -> None:
        await self._transition(LifecycleState.RUNNING, EventType.RUNTIME_RESUMED)

    async def stop(self) -> None:
        await self._transition(LifecycleState.STOPPED, EventType.RUNTIME_STOPPED)

    async def shutdown(self) -> None:
        await self._transition(LifecycleState.SHUTDOWN, EventType.RUNTIME_SHUTDOWN)

    def status(self) -> dict[str, object]:
        return {
            "schema_version": "1.1",
            "state": self._state.value,
            "events": len(self.events.history()),
            "input_enabled": False,
        }
