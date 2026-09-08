from __future__ import annotations

import unittest

from tests.helpers import identity
from tests.unit.test_capture_registry import FakeBackend
from uga.capture.registry import CaptureBackendRegistry
from uga.capture.ring_buffer import FrameRingBuffer
from uga.capture.session import CaptureSession
from uga.core.events import EventBus, EventType
from uga.time.clock import ManualClock


class CaptureSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_publishes_capture_and_drop_telemetry(self) -> None:
        registry = CaptureBackendRegistry(("fake",))
        registry.register(FakeBackend("fake", True, 100))
        events = EventBus(ManualClock(10))
        session = CaptureSession(identity(), registry, FrameRingBuffer(1), events)

        self.assertEqual(await session.start(), "fake")
        await session.capture_once()
        await session.capture_once()
        await session.stop()

        event_types = [event.event_type for event in events.history()]
        self.assertEqual(event_types.count(EventType.FRAME_CAPTURED), 2)
        self.assertIn(EventType.FRAME_DROPPED, event_types)

    async def test_failed_backend_can_recover_to_next_candidate(self) -> None:
        class CaptureFailingBackend(FakeBackend):
            def _capture(self):  # type: ignore[no-untyped-def,override]
                raise RuntimeError("device lost")

        registry = CaptureBackendRegistry(("unstable", "fallback"))
        registry.register(CaptureFailingBackend("unstable", True, 100))
        registry.register(FakeBackend("fallback", True, 10))
        session = CaptureSession(identity(), registry, FrameRingBuffer(), EventBus(ManualClock()))

        await session.start()
        with self.assertRaisesRegex(RuntimeError, "device lost"):
            await session.capture_once()
        self.assertEqual(await session.recover(), "fallback")
        await session.stop()


if __name__ == "__main__":
    unittest.main()
