from __future__ import annotations

import unittest

from uga.core.errors import ClockRegressionError, ContractViolation
from uga.time.clock import ManualClock, PerfCounterClock
from uga.time.timeline import MonotonicTimeline


class ClockTimelineTests(unittest.TestCase):
    def test_perf_counter_is_monotonic(self) -> None:
        clock = PerfCounterClock()
        first = clock.now()
        second = clock.now()
        self.assertGreaterEqual(second, first)

    def test_timeline_sequences_equal_timestamps(self) -> None:
        clock = ManualClock(50)
        timeline = MonotonicTimeline(clock)
        first = timeline.stamp("capture")
        second = timeline.stamp("observation")
        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertEqual(first.timestamp, second.timestamp)

    def test_timeline_rejects_regression(self) -> None:
        clock = ManualClock(10)
        timeline = MonotonicTimeline(clock)
        timeline.stamp("first")
        clock.set(9)
        with self.assertRaises(ClockRegressionError):
            timeline.stamp("second")

    def test_manual_clock_cannot_advance_backwards(self) -> None:
        with self.assertRaises(ContractViolation):
            ManualClock().advance(-1)


if __name__ == "__main__":
    unittest.main()
