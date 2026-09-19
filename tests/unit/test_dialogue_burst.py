from __future__ import annotations

import unittest
from dataclasses import replace

from tests.helpers import frame
from uga.gui.dialogue_burst import DialogueBurstGate
from uga.time.clock import UGATime


class DialogueBurstGateTests(unittest.TestCase):
    def test_one_ocr_confirmation_authorizes_three_spaced_clicks(self) -> None:
        gate = DialogueBurstGate(
            interval_ns=250_000_000,
            confirmation_ttl_ns=1_250_000_000,
            clicks_per_confirmation=3,
        )
        source = frame(1, timestamp_ns=0)
        gate.arm(
            x=0.96,
            y=0.915,
            validated_frame=source,
            task_generation=4,
            now=UGATime(0),
        )

        self.assertIsNone(gate.take_due(UGATime(249_000_000)))
        first = gate.take_due(UGATime(250_000_000))
        second = gate.take_due(UGATime(500_000_000))
        third = gate.take_due(UGATime(750_000_000))

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNotNone(third)
        self.assertIsNone(gate.take_due(UGATime(1_000_000_000)))
        assert first is not None
        self.assertTrue(gate.permit_is_current(first, UGATime(1_000_000_000)))

    def test_fresh_ocr_refreshes_budget_and_non_dialogue_revokes_immediately(self) -> None:
        gate = DialogueBurstGate(clicks_per_confirmation=1)
        source = frame(1, timestamp_ns=0)
        gate.arm(
            x=0.96,
            y=0.915,
            validated_frame=source,
            task_generation=2,
            now=UGATime(0),
        )
        self.assertIsNotNone(gate.take_due(UGATime(250_000_000)))
        self.assertIsNone(gate.take_due(UGATime(500_000_000)))

        refreshed = replace(source, frame_id="refreshed", capture_timestamp=UGATime(500_000_000))
        gate.refresh(
            dialogue_active=True,
            validated_frame=refreshed,
            task_generation=2,
            now=UGATime(500_000_000),
        )
        self.assertIsNotNone(gate.take_due(UGATime(750_000_000)))

        gate.refresh(
            dialogue_active=False,
            validated_frame=refreshed,
            task_generation=2,
            now=UGATime(760_000_000),
        )
        self.assertFalse(gate.active)
        self.assertIsNone(gate.take_due(UGATime(1_000_000_000)))

    def test_expired_confirmation_cannot_click(self) -> None:
        gate = DialogueBurstGate(
            interval_ns=200_000_000,
            confirmation_ttl_ns=400_000_000,
        )
        gate.arm(
            x=0.96,
            y=0.915,
            validated_frame=frame(1, timestamp_ns=0),
            task_generation=1,
            now=UGATime(0),
        )

        self.assertIsNone(gate.take_due(UGATime(401_000_000)))
        self.assertFalse(gate.active)


if __name__ == "__main__":
    unittest.main()
