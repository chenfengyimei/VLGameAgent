from __future__ import annotations

import asyncio
import threading
import time
import unittest

from tests.integration.test_agent_loop import GroundedClickPlanner
from tests.integration.test_review_followup import make_loop
from uga.core.deadline import BoundedWorker, Deadline, DeadlineExceeded, remaining_timeout
from uga.policy.model_capabilities import model_request_options
from uga.policy.vision_transport import ProviderError
from uga.policy.vlm_planner import OpenAICompatibleVisionClient
from uga.time.clock import ManualClock


class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_nested_requests_share_one_budget(self) -> None:
        worker, deadline = BoundedWorker(), Deadline.after(1)
        first = await worker.run(deadline, remaining_timeout, 30.0)
        await asyncio.sleep(0.02)
        second = await worker.run(deadline, remaining_timeout, 30.0)
        self.assertLessEqual(first, 1)
        self.assertLess(second, first)

    async def test_hung_worker_is_bounded_and_never_reused(self) -> None:
        release, worker = threading.Event(), BoundedWorker()
        start = time.monotonic()
        try:
            with self.assertRaises(DeadlineExceeded):
                await worker.run(Deadline.after(0.03), release.wait, 2)
            self.assertLess(time.monotonic() - start, 0.5)
            with self.assertRaises(DeadlineExceeded):
                await worker.run(Deadline.after(1), lambda: None)
        finally:
            release.set()

    async def test_cancel_does_not_wait_for_pool_threads(self) -> None:
        release, entered = threading.Event(), threading.Event()

        def slow():  # type: ignore[no-untyped-def]
            entered.set()
            release.wait(2)

        task = asyncio.create_task(BoundedWorker().run(Deadline.after(5), slow))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.001)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.2)
        finally:
            release.set()

    async def test_real_loop_stops_hung_planner_without_input(self) -> None:
        release = threading.Event()

        class Slow(GroundedClickPlanner):
            def decide(self, **kwargs):  # type: ignore[no-untyped-def]
                release.wait(2)
                return super().decide(**kwargs)

        loop, backend, _, _, _ = make_loop(Slow(), ManualClock(100))
        loop._decision_timeout_s = 0.03
        try:
            with self.assertRaises(ProviderError) as raised:
                await loop.step()
            self.assertTrue(raised.exception.fatal)
            self.assertFalse(backend.actions)
        finally:
            release.set()

    def test_invalid_budgets(self) -> None:
        for value in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Deadline.after(value)


class ModelCapabilityTests(unittest.TestCase):
    def test_glm53_cannot_disable_thinking_or_override_output_budget(self) -> None:
        for kwargs in (
            {"disable_thinking": True},
            {"extra_body": {"thinking": {"type": "disabled"}}},
            {"extra_body": {"enable_thinking": False}},
            {"extra_body": {"reasoning_effort": "none"}},
            {"extra_body": {"max_tokens": 9999999}},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                OpenAICompatibleVisionClient(
                    base_url="https://provider.test/v1", model="glm-5.3-flash", **kwargs
                )

    def test_glm53_and_legacy_options_are_separate(self) -> None:
        for name in ("glm-5.3-flash", "ZHIPU/GLM-5.3-Flash"):
            self.assertEqual(
                model_request_options(
                    name, disable_thinking=False, extra_body={"reasoning_effort": "low"}
                )["thinking"],
                {"type": "enabled"},
            )
        self.assertEqual(
            model_request_options("glm-4.6v", disable_thinking=True, extra_body=None),
            {"thinking": {"type": "disabled"}},
        )
