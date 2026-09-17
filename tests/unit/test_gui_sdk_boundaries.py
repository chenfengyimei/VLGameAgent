from __future__ import annotations

import unittest

from uga.control.lifetime import ActionLifetime
from uga.core.errors import ContractViolation
from uga.gui.schema import GuiAction, GuiActionKind
from uga.time.clock import UGATime


class GuiSdkBoundaryTests(unittest.TestCase):
    def test_short_long_click_rejected_before_translation(self) -> None:
        for duration in (1, 699_999_999, 700_000_000):
            with self.subTest(duration=duration), self.assertRaises(ContractViolation):
                GuiAction(
                    "a",
                    GuiActionKind.LONG_CLICK,
                    ActionLifetime(UGATime(0), UGATime(0), UGATime(duration)),
                    x=0.5,
                    y=0.5,
                )

    def test_duplicate_or_invalid_key_codes_reject_before_any_partial_input(self) -> None:
        for codes in ((17, 17), (True,), (0,), (256,), (17.0,)):
            with self.subTest(codes=codes), self.assertRaises(ContractViolation):
                GuiAction(
                    "a",
                    GuiActionKind.HOTKEY,
                    ActionLifetime(UGATime(0), UGATime(0), UGATime(1)),
                    key_codes=codes,
                )

    def test_bool_or_nonfinite_coordinates_and_confidence_reject(self) -> None:
        for kwargs in ({"x": True}, {"x": float("inf")}, {"y": float("nan")}, {"confidence": True}):
            values = {"x": 0.5, "y": 0.5, **kwargs}
            with self.subTest(values=values), self.assertRaises(ContractViolation):
                GuiAction(
                    "a",
                    GuiActionKind.CLICK,
                    ActionLifetime(UGATime(0), UGATime(0), UGATime(1)),
                    **values,
                )
