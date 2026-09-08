from __future__ import annotations

import unittest

from tests.helpers import frame
from uga.capture.ring_buffer import FrameRingBuffer, LatestFrameSlot
from uga.core.errors import ClockRegressionError


class RingBufferTests(unittest.TestCase):
    def test_ring_is_bounded_and_latest_wins(self) -> None:
        ring = FrameRingBuffer(capacity=2)
        ring.publish(frame(1))
        ring.publish(frame(2))
        latest = ring.publish(frame(3))
        self.assertEqual([item.frame.frame_id for item in ring.snapshot()], ["frame-2", "frame-3"])
        self.assertEqual(ring.latest(), latest)
        self.assertEqual(ring.dropped, 1)

    def test_ring_rejects_clock_regression(self) -> None:
        ring = FrameRingBuffer()
        ring.publish(frame(1, timestamp_ns=10))
        with self.assertRaises(ClockRegressionError):
            ring.publish(frame(2, timestamp_ns=9))

    def test_policy_slot_replaces_unconsumed_frame(self) -> None:
        ring = FrameRingBuffer()
        slot = LatestFrameSlot()
        slot.offer(ring.publish(frame(1)))
        newest = ring.publish(frame(2))
        slot.offer(newest)
        self.assertEqual(slot.take(), newest)
        self.assertEqual(slot.replaced, 1)
        self.assertIsNone(slot.take())


if __name__ == "__main__":
    unittest.main()
