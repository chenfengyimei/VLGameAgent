"""Regression coverage for contracts crossing the PR2/PR3 integration boundary."""
from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.agent.__main__ import _has_fatal_provider_error, cli
from apps.agent.run import build_parser
from tests.helpers import frame
from tests.integration import test_gui_reference_export as gui_fixtures
from tests.unit.test_closed_loop_supervisor import snapshot
from uga.agent.closed_loop import ClosedLoopSupervisor
from uga.capture.hub import CaptureHub
from uga.capture.ring_buffer import FrameRingBuffer
from uga.core.deadline import CURRENT_DEADLINE, Deadline
from uga.core.errors import ContractViolation
from uga.dataset.gui_export import export_gui_samples
from uga.environment.profile import PerceptionProfile
from uga.gui.schema import GuiActionKind
from uga.policy.call_budget import CallBudget, DeadlineWorker, decision_budget, request_timeout
from uga.policy.model_policy import validated_extensions
from uga.policy.vision_transport import ProviderError
from uga.recording.frame_queue import RecordingFailure
from uga.time.clock import ManualClock


class MergedContractTests(unittest.TestCase):
    def test_console_keeps_both_deadlines_and_startup_safety_options(self) -> None:
        options = [
            '--profile', 'fixture.yaml', '--decision-timeout-seconds', '12',
            '--perception-timeout-seconds', '3', '--watchdog-timeout-seconds', '7',
        ]
        expected = vars(build_parser().parse_args(options))
        with (
            patch('sys.argv', ['uga-agent', 'run', *options]),
            patch('apps.agent.__main__._run_safely', return_value=0) as run,
            self.assertRaises(SystemExit),
        ):
            cli()
        actual = vars(run.call_args.args[0]).copy()
        actual.pop('command')
        self.assertEqual(expected, actual)

    def test_main_capture_keywords_preserve_bounded_recorder(self) -> None:
        hub = CaptureHub(
            primary=None, frames=FrameRingBuffer(), capture_operation_timeout_s=0.3,
            recording_max_frames=2, recording_max_bytes=100, recording_close_timeout_s=0.1,
        )
        self.assertEqual(hub._capture_call_timeout_s, 0.3)
        self.assertEqual(hub._recorder_capacity, 2)
        self.assertEqual(hub._recorder_max_bytes, 100)
        self.assertEqual(hub._recorder_close_timeout_s, 0.1)

    def test_both_deadline_contexts_limit_nested_http_requests(self) -> None:
        token = CURRENT_DEADLINE.set(Deadline.after(0.2))
        try:
            with decision_budget(60):
                self.assertLessEqual(request_timeout(90), 0.2)
        finally:
            CURRENT_DEADLINE.reset(token)

    def test_prefixed_models_keep_capability_and_budget_constraints(self) -> None:
        for model in ('vendor/glm-5.3-flash', 'vendor/glm-5.3', 'GLM-5.3-FLASH'):
            for extra, disabled, budget in (
                ({'reasoning_effort': 'medium'}, False, 4096),
                ({'enable_thinking': False}, False, 4096),
                ({}, True, 4096),
                ({}, False, 64),
                ({'tools': []}, False, 4096),
            ):
                with self.subTest(model=model, extra=extra), self.assertRaises(ContractViolation):
                    validated_extensions(
                        model, extra_body=extra, disable_thinking=disabled, max_output_tokens=budget
                    )
            self.assertEqual(validated_extensions(
                model, extra_body={'reasoning_effort': 'low'},
                disable_thinking=False, max_output_tokens=4096,
            )['thinking'], {'type': 'enabled'})

    def test_reference_export_cannot_mutate_source_episode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            episode = gui_fixtures.GuiTrainingExportTests()._episode(
                Path(temp), GuiActionKind.CLICK
            )
            checksum = (episode / 'checksum.json').read_bytes()
            with self.assertRaisesRegex(ContractViolation, 'outside'):
                export_gui_samples((episode,), episode / 'checksum.json')
            self.assertEqual((episode / 'checksum.json').read_bytes(), checksum)

    def test_sensitive_observation_resets_goal_evidence_before_resume(self) -> None:
        supervisor = ClosedLoopSupervisor(
            ManualClock(100), PerceptionProfile(), goal_evidence=('complete',)
        )
        first = snapshot(1, 10, visible_text=('complete',))
        self.assertFalse(supervisor._goal.consider_observed_evidence(first))
        gate = snapshot(2, 20, visible_text=('password', 'complete'))
        effect = supervisor.observe(gate, frame(2, timestamp_ns=20))
        self.assertIsNone(effect.effect_observed)
        self.assertFalse(supervisor._goal.consider_observed_evidence(
            snapshot(3, 600_000_000, visible_text=('complete',))
        ))


class MergedAsyncContracts(unittest.IsolatedAsyncioTestCase):
    async def test_both_stalled_capture_sources_fail_fatally_without_retries(self) -> None:
        release = threading.Event()

        class Stuck:
            calls = 0
            def capture(self):
                self.calls += 1
                release.wait(2)
                return frame(1)

        primary, fallback = Stuck(), Stuck()
        hub = CaptureHub(
            primary=primary, fallback=fallback, frames=FrameRingBuffer(),
            capture_operation_timeout_s=0.04, fallback_after_s=0.01,
        )
        try:
            with self.assertRaises(ExceptionGroup) as caught:
                await asyncio.wait_for(hub.run(asyncio.Event()), 1)
            self.assertTrue(_has_fatal_provider_error(caught.exception))
            self.assertEqual((primary.calls, fallback.calls), (1, 1))
        finally:
            release.set()

    async def test_idle_capture_does_not_hide_async_recording_failure(self) -> None:
        release = threading.Event()

        class OnceThenIdle:
            calls = 0
            def capture(self):
                self.calls += 1
                if self.calls > 1:
                    release.wait(2)
                return frame(self.calls)

        def broken_recorder(value):
            raise OSError('simulated disk failure')

        hub = CaptureHub(
            primary=OnceThenIdle(), frames=FrameRingBuffer(), record_frame=broken_recorder,
            recording_close_timeout_s=0.1,
        )
        try:
            with self.assertRaises(RecordingFailure) as caught:
                await asyncio.wait_for(hub.run(asyncio.Event()), 1)
            self.assertTrue(_has_fatal_provider_error(caught.exception))
            self.assertFalse(hub.recording_complete)
        finally:
            release.set()

    async def test_run_context_cancels_stalled_worker_and_retires_it(self) -> None:
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()

        def blocked():
            entered.set()
            release.wait(2)
            return 1

        worker = DeadlineWorker('merge-cancel-test')
        task = asyncio.create_task(worker.call(blocked, CallBudget(5, cancelled=cancelled.is_set)))
        try:
            while not entered.is_set():
                await asyncio.sleep(0.001)
            cancelled.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            with self.assertRaisesRegex(ProviderError, 'retired'):
                await worker.call(lambda: 2, CallBudget(1))
        finally:
            release.set()
