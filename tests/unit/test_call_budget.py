from __future__ import annotations

import asyncio
import threading
import time
import unittest
from unittest.mock import patch

from tests.integration.test_agent_loop import GroundedClickPlanner
from tests.integration.test_review_followup import make_loop
from uga.policy.call_budget import CallBudget, DeadlineWorker, decision_budget, request_timeout
from uga.policy.vision_transport import ProviderError
from uga.time.clock import ManualClock


class BudgetTests(unittest.TestCase):
    def test_nested_requests_share_deadline(self) -> None:
        with (
            patch("uga.policy.call_budget.time.monotonic", return_value=100),
            decision_budget(10) as original,
        ):
            self.assertEqual(request_timeout(30), 10)
            with (
                patch("uga.policy.call_budget.time.monotonic", return_value=107),
                decision_budget(60) as inherited,
            ):
                self.assertIs(inherited, original)
                self.assertEqual(request_timeout(30), 3)
                with (
                    patch("uga.policy.call_budget.time.monotonic", return_value=111),
                    self.assertRaises(ProviderError),
                ):
                    request_timeout(30)

    def test_request_count_is_shared_and_bounded(self) -> None:
        with decision_budget(10):
            for _ in range(4):
                request_timeout(3)
            with self.assertRaisesRegex(ProviderError, "budget exhausted"):
                request_timeout(3)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_stuck_call_is_retired_without_waiting_for_thread(self) -> None:
        release = threading.Event()
        worker = DeadlineWorker("test-deadline")
        try:
            begin = time.monotonic()
            with self.assertRaises(ProviderError) as caught:
                await worker.call(lambda: release.wait(2), CallBudget(0.02))
            self.assertTrue(caught.exception.fatal)
            self.assertLess(time.monotonic() - begin, 0.5)
            with self.assertRaisesRegex(ProviderError, "retired"):
                await worker.call(lambda: True, CallBudget(1))
        finally:
            release.set()
            await asyncio.sleep(0.01)

    async def test_cancelled_worker_cannot_be_reused(self) -> None:
        release = threading.Event()
        entered = threading.Event()
        def blocked() -> int:
            entered.set()
            release.wait(2)
            return 123
        worker = DeadlineWorker("test-cancel")
        task = asyncio.create_task(worker.call(blocked, CallBudget(5)))
        try:
            while not entered.is_set():
                await asyncio.sleep(0.001)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with self.assertRaises(ProviderError):
                await worker.call(lambda: 0, CallBudget(1))
        finally:
            release.set()
            await asyncio.sleep(0.01)

    async def test_late_model_has_no_input_and_no_unbounded_join(self) -> None:
        release = threading.Event()
        class SlowPlanner(GroundedClickPlanner):
            def decide(self, **kwargs):  # type: ignore[no-untyped-def]
                release.wait(2)
                return super().decide(**kwargs)
        loop, backend, _, _, _ = make_loop(SlowPlanner(), ManualClock(100))
        loop._decision_timeout_s = 0.02
        try:
            with self.assertRaises(ProviderError) as caught:
                await asyncio.wait_for(loop.step(), 1)
            self.assertTrue(caught.exception.fatal)
            self.assertEqual(backend.actions, [])
        finally:
            release.set()
            await asyncio.sleep(0.01)
        self.assertEqual(backend.actions, [])

    async def test_run_stop_cancels_blocked_planner_before_it_returns(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        class SlowPlanner(GroundedClickPlanner):
            def decide(self, **kwargs):  # type: ignore[no-untyped-def]
                entered.set()
                release.wait(2)
                return super().decide(**kwargs)
        loop, backend, _, _, _ = make_loop(SlowPlanner(), ManualClock(100))
        stop = asyncio.Event()
        task = asyncio.create_task(loop.run(stop))
        try:
            while not entered.is_set():
                await asyncio.sleep(0.001)
            stop.set()
            await asyncio.wait_for(task, 0.5)
            self.assertFalse(release.is_set())
            self.assertEqual(backend.actions, [])
        finally:
            release.set()
            await asyncio.sleep(0.01)
