"""No rounding tolerance: returned relative budgets may never gain time."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from uga.core.deadline import CURRENT_DEADLINE, Deadline, DeadlineExceeded
from uga.policy.call_budget import CallBudget, decision_budget, request_timeout
from uga.policy.vision_transport import ProviderError


class DeadlinePrecisionTests(unittest.TestCase):
    def test_operation_budget_does_not_round_up_on_same_clock_tick(self) -> None:
        for start in (1000.0, 1_000_000.0, 1_000_000_000.0):
            for duration in (0.2, 0.03, 0.007):
                with self.subTest(start=start, duration=duration), patch(
                    'time.monotonic', return_value=start
                ):
                    self.assertLessEqual(Deadline.after(duration).remaining(), duration)

    def test_model_budget_does_not_round_up_on_same_clock_tick(self) -> None:
        for start in (1000.0, 1_000_000.0, 1_000_000_000.0):
            for duration in (0.2, 0.03, 0.007):
                with self.subTest(start=start, duration=duration), patch(
                    'time.monotonic', return_value=start
                ):
                    self.assertLessEqual(CallBudget(duration).remaining(), duration)

    def test_elapsed_time_is_deducted_without_absolute_timestamp_cancellation(self) -> None:
        with patch('time.monotonic', return_value=1000.0) as clock:
            operation, model = Deadline.after(0.2), CallBudget(0.2)
            clock.return_value = 1000.125
            for budget in (operation, model):
                self.assertGreater(budget.remaining(), 0)
                self.assertLessEqual(budget.remaining(), 0.2 - 0.125)
            clock.return_value = 1000.25
            with self.assertRaises(DeadlineExceeded):
                operation.remaining()
            with self.assertRaises(ProviderError):
                model.remaining()

    def test_nested_http_budget_respects_original_and_elapsed_operation_limit(self) -> None:
        with patch('time.monotonic', return_value=1000.0) as clock:
            token = CURRENT_DEADLINE.set(Deadline.after(0.2))
            try:
                with decision_budget(60):
                    self.assertLessEqual(request_timeout(90), 0.2)
                    clock.return_value = 1000.125
                    self.assertLessEqual(request_timeout(90), 0.2 - 0.125)
            finally:
                CURRENT_DEADLINE.reset(token)

    def test_explicit_absolute_deadline_and_cancellation_remain_supported(self) -> None:
        with patch('time.monotonic', return_value=1000.0):
            absolute = Deadline(1000.5)
            self.assertEqual(absolute.remaining(), 0.5)
            absolute.aborted.set()
            with self.assertRaises(DeadlineExceeded):
                absolute.remaining()
            model = CallBudget(0.2)
            model.cancel()
            with self.assertRaises(ProviderError):
                model.remaining()
