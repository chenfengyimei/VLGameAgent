from __future__ import annotations

import unittest

from tests.unit.test_closed_loop_supervisor import snapshot
from uga.agent.progress import LoopDetector, LoopKind, LoopRecord, ProgressTracker


class ProgressTrackerTests(unittest.TestCase):
    def test_visual_animation_without_semantic_change_is_not_progress(self) -> None:
        before = ProgressTracker.state(snapshot(1, 0, signature="visual-a"))
        after = ProgressTracker.state(snapshot(2, 1, signature="visual-b"))

        self.assertFalse(
            ProgressTracker.progressed(
                before,
                after,
                target_effect_observed=False,
            )
        )


class LoopDetectorTests(unittest.TestCase):
    def test_same_ineffective_action_is_detected_on_third_record(self) -> None:
        detector = LoopDetector()
        record = LoopRecord("state-a", "click:settings", False, "state-a", False)

        self.assertIsNone(detector.record(record))
        self.assertIsNone(detector.record(record))
        finding = detector.record(record)

        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding.kind, LoopKind.REPEATED_INEFFECTIVE_ACTION)

    def test_two_step_state_action_ring_is_detected_after_two_rounds(self) -> None:
        detector = LoopDetector()
        records = (
            LoopRecord("state-a", "click:next", False, "state-b", False),
            LoopRecord("state-b", "click:back", False, "state-a", False),
            LoopRecord("state-a", "click:next", False, "state-b", False),
            LoopRecord("state-b", "click:back", False, "state-a", False),
        )

        findings = tuple(detector.record(record) for record in records)

        self.assertIsNone(findings[2])
        self.assertIsNotNone(findings[3])
        assert findings[3] is not None
        self.assertEqual(findings[3].kind, LoopKind.STATE_ACTION_CYCLE)
        self.assertEqual(findings[3].cycle_length, 2)

    def test_every_cycle_length_from_two_through_six_is_bounded(self) -> None:
        for length in range(2, 7):
            with self.subTest(length=length):
                detector = LoopDetector()
                cycle = tuple(
                    LoopRecord(
                        f"state-{index}",
                        f"action-{index}",
                        False,
                        f"state-{(index + 1) % length}",
                        False,
                    )
                    for index in range(length)
                )
                findings = tuple(detector.record(item) for item in (*cycle, *cycle))

                self.assertIsNone(findings[-2])
                self.assertIsNotNone(findings[-1])
                assert findings[-1] is not None
                self.assertEqual(findings[-1].cycle_length, length)

    def test_repeated_state_action_ring_with_semantic_progress_is_not_a_loop(self) -> None:
        detector = LoopDetector()
        cycle = (
            LoopRecord("state-a", "click:next", True, "state-b", True),
            LoopRecord("state-b", "click:back", True, "state-a", True),
        )

        findings = tuple(detector.record(item) for item in (*cycle, *cycle))

        self.assertTrue(all(finding is None for finding in findings))


if __name__ == "__main__":
    unittest.main()
