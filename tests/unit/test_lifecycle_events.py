from __future__ import annotations

import unittest

from uga.core.errors import ContractViolation
from uga.core.events import Event, EventType
from uga.core.lifecycle import LifecycleState
from uga.core.runtime import AgentRuntime
from uga.time.clock import ManualClock


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_lifecycle_and_events(self) -> None:
        clock = ManualClock(1)
        runtime = AgentRuntime(clock)
        observed: list[Event] = []
        runtime.events.subscribe(observed.append)
        await runtime.start()
        await runtime.pause()
        await runtime.resume()
        await runtime.stop()
        await runtime.shutdown()
        self.assertEqual(runtime.state, LifecycleState.SHUTDOWN)
        self.assertEqual(
            [event.event_type for event in observed],
            [
                EventType.RUNTIME_STARTED,
                EventType.RUNTIME_PAUSED,
                EventType.RUNTIME_RESUMED,
                EventType.RUNTIME_STOPPED,
                EventType.RUNTIME_SHUTDOWN,
            ],
        )

    async def test_invalid_transition_fails(self) -> None:
        runtime = AgentRuntime(ManualClock())
        with self.assertRaises(ContractViolation):
            await runtime.pause()


if __name__ == "__main__":
    unittest.main()
