"""System-boundary regressions from the a15d6d0 follow-up review.

All input is DryRun: these tests must never send physical input or call a
remote model.  Delays use ManualClock rather than sleeps.
"""

from __future__ import annotations

import unittest

from tests.helpers import frame, identity
from tests.integration.test_agent_loop import FixedVisualCaptureSource, GroundedClickPlanner
from tests.integration.test_baseline_agent import FakeIntegrity, FakeWindows, profile
from tests.unit.test_closed_loop_supervisor import outcome, snapshot
from uga.agent.closed_loop import ClosedLoopSupervisor, DecisionDisposition, GoalVerifier
from uga.agent.mode_router import ModeRouter, RuleModeClassifier
from uga.agent.recovery_budget import RecoveryBudget
from uga.agent.session_state import GameSessionState
from uga.capture.ring_buffer import FrameRingBuffer
from uga.control.arbiter import ActionArbiter
from uga.control.executor import InputExecutor
from uga.control.input_backend import DryRunInputBackend
from uga.control.lease import ControlMode
from uga.control.lease_manager import ControlLeaseManager
from uga.control.scheduler import ActionScheduler
from uga.core.agent_loop import RealtimeAgentLoop
from uga.core.events import EventBus, EventType
from uga.environment.generic import GenericEnvironment
from uga.environment.profile import PerceptionProfile
from uga.gui.controller import GuiActionController
from uga.observation.buffer import TemporalObservationBuffer
from uga.observation.builder import ObservationBuilder, ObservationInputs
from uga.perception.builder import PerceptionBuilder
from uga.perception.schema import DecisionKind, PlannerOutcome
from uga.perception.text import NullTextProvider
from uga.policy.chunk_controller import ActionChunkController
from uga.policy.vision_transport import ProviderError, ProviderErrorKind, VisionRateLimitedError
from uga.safety.focus_guard import AgentEnableState, FocusGuard
from uga.time.clock import ManualClock, UGATime


def make_loop(planner, clock, *, session=None, recorder=None):  # type: ignore[no-untyped-def]
    frames = FrameRingBuffer()
    leases = ControlLeaseManager(clock)
    backend = DryRunInputBackend()
    executor = InputExecutor(
        clock,
        backend,
        FocusGuard(FakeWindows(identity()), FakeIntegrity(), leases, AgentEnableState(True)),
        leases,
    )
    scheduler = ActionScheduler(clock, executor, leases)
    arbiter = ActionArbiter(clock, leases)
    environment = GenericEnvironment(profile())
    supervisor = ClosedLoopSupervisor(clock, profile().perception, session=session)
    events = EventBus(clock)
    loop = RealtimeAgentLoop(
        clock=clock,
        capture=FixedVisualCaptureSource(frames, clock),
        frames=frames,
        observation_builder=ObservationBuilder(
            clock, "fixture-game", ObservationInputs("settings")
        ),
        observations=TemporalObservationBuffer(),
        environment=environment,
        mode_classifier=RuleModeClassifier(),
        mode_router=ModeRouter(ControlMode.GUI, confirmation_frames=1, started_at=UGATime(0)),
        policy=None,
        leases=leases,
        controller=ActionChunkController(environment, arbiter, scheduler),
        scheduler=scheduler,
        events=events,
        grounded_planner=planner,
        recorder=recorder,
        perception_builder=PerceptionBuilder(NullTextProvider()),
        closed_loop=supervisor,
        gui_controller=GuiActionController(arbiter, scheduler, recorder),
        key_resolver=lambda _: None,
    )
    return loop, backend, supervisor, scheduler, frames


class ReviewLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_frame_expires_after_acceptance_but_before_input(self) -> None:
        clock = ManualClock(100)
        loop, backend, _, _, _ = make_loop(GroundedClickPlanner(), clock)
        loop._events.subscribe(
            lambda event: (
                clock.set(60_000_000_100) if event.event_type == EventType.ACTION_ACCEPTED else None
            )
        )
        result = await loop.step()
        self.assertIsNotNone(result.gui_submission)
        self.assertEqual(result.scheduler_stats.executed, 0)
        self.assertEqual(backend.actions, [])

    async def test_frozen_capture_never_gets_new_action_lifetime(self) -> None:
        clock = ManualClock(100)

        class SlowPlanner(GroundedClickPlanner):
            def decide(self, **kwargs: object) -> PlannerOutcome:
                result = super().decide(**kwargs)
                clock.set(60_000_000_100)
                return result

        loop, backend, _, _, _ = make_loop(SlowPlanner(), clock)
        result = await loop.step()
        self.assertEqual(backend.actions, [])
        self.assertIsNone(result.gui_submission)

    async def test_task_change_during_inference_invalidates_old_result(self) -> None:
        clock = ManualClock(100)
        session = GameSessionState()

        class TaskChangingPlanner(GroundedClickPlanner):
            def decide(self, **kwargs: object) -> PlannerOutcome:
                result = super().decide(**kwargs)
                session.task_generation += 1
                return result

        loop, backend, _, _, _ = make_loop(TaskChangingPlanner(), clock, session=session)
        result = await loop.step()
        self.assertEqual(backend.actions, [])
        self.assertIsNone(result.gui_submission)

    async def test_auth_failure_escapes_real_loop_retry_boundary(self) -> None:
        clock = ManualClock(100)

        class AuthPlanner(GroundedClickPlanner):
            def decide(self, **kwargs: object) -> PlannerOutcome:
                raise ProviderError(
                    ProviderErrorKind.AUTH, "bad credentials", fatal=True, status=401
                )

        loop, backend, _, _, _ = make_loop(AuthPlanner(), clock)
        with self.assertRaises(ProviderError):
            await loop.step()
        self.assertEqual(backend.actions, [])

    async def test_rate_limit_respects_retry_after(self) -> None:
        clock = ManualClock(100)

        class LimitedPlanner(GroundedClickPlanner):
            calls = 0

            def decide(self, **kwargs: object) -> PlannerOutcome:
                self.calls += 1
                raise VisionRateLimitedError("slow down", retry_after_s=45.0)

        planner = LimitedPlanner()
        loop, backend, _, _, _ = make_loop(planner, clock)
        await loop.step()
        clock.set(20_000_000_100)
        await loop.step()
        self.assertEqual(planner.calls, 1)
        self.assertEqual(backend.actions, [])


class ReviewSupervisorTests(unittest.TestCase):
    def test_repeat_escape_cannot_bypass_zero_recovery_budget(self) -> None:
        budget = RecoveryBudget(0)
        supervisor = ClosedLoopSupervisor(
            ManualClock(100),
            PerceptionProfile(recovery_safe_actions=("back",)),
            max_recoveries=0,
            recovery_budget=budget,
            back_hotspot=(0.05, 0.10),
        )
        proposal = outcome(1, label="upgrade")
        perceived = snapshot(1, 100)
        source = frame(1, timestamp_ns=100)
        supervisor.start_action(proposal, perceived, source)
        supervisor.start_action(proposal, perceived, source)
        result = supervisor.assess(proposal, perceived, perceived, source, source, "upgrade")
        self.assertEqual(result.disposition, DecisionDisposition.BLOCK)
        self.assertEqual(budget.consumed, 0)

    def test_progress_button_exemption_does_not_poison_escape_cache(self) -> None:
        supervisor = ClosedLoopSupervisor(
            ManualClock(100),
            PerceptionProfile(recovery_safe_actions=("back",)),
        )
        proposal = outcome(1, label="\u7ee7\u7eed")
        perceived = snapshot(1, 100)
        source = frame(1, timestamp_ns=100)
        supervisor.start_action(proposal, perceived, source)
        supervisor.start_action(proposal, perceived, source)
        assert proposal.action is not None
        for _ in range(4):
            self.assertFalse(supervisor._should_escape_repeated_action(proposal.action))

    def test_unrelated_screen_text_is_not_a_registered_goal_predicate(self) -> None:
        verifier = GoalVerifier()
        for number, when in ((1, 0), (2, 600_000_000), (3, 1_200_000_000)):
            self.assertFalse(
                verifier.consider(
                    outcome(number, kind=DecisionKind.DONE),
                    snapshot(number, when, visible_text=("settings",)),
                )
            )

    def test_explicit_goal_evidence_remains_supported(self) -> None:
        verifier = GoalVerifier(required_evidence=("destination",))
        self.assertFalse(
            verifier.consider(
                outcome(1, kind=DecisionKind.DONE),
                snapshot(1, 0, visible_text=("destination",)),
            )
        )
        self.assertTrue(
            verifier.consider(
                outcome(2, kind=DecisionKind.DONE),
                snapshot(2, 600_000_000, visible_text=("destination",)),
            )
        )

    def test_old_frame_rejected_by_execution_context(self) -> None:
        supervisor = ClosedLoopSupervisor(ManualClock(60_000_000_100), PerceptionProfile())
        source = frame(1, timestamp_ns=100)
        allowed, _ = supervisor.validate_execution_context(source, source)
        self.assertFalse(allowed)
