from __future__ import annotations

import unittest
from dataclasses import replace

from tests.helpers import frame, identity
from tests.integration.test_baseline_agent import FakeIntegrity, FakeWindows, profile
from uga.agent.closed_loop import ClosedLoopSupervisor, DecisionDisposition
from uga.agent.mode_router import ModeRouter, RuleModeClassifier
from uga.capture.frame import Frame
from uga.capture.ring_buffer import FrameRingBuffer, SequencedFrame
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
from uga.gui.schema import GuiActionKind
from uga.observation.buffer import TemporalObservationBuffer
from uga.observation.builder import ObservationBuilder, ObservationInputs
from uga.perception.builder import PerceptionBuilder
from uga.perception.schema import (
    DecisionKind,
    GoalStatus,
    GroundedAction,
    NormalizedBox,
    PerceptionSnapshot,
    PlannerOutcome,
)
from uga.perception.text import NullTextProvider
from uga.policy.action_chunk import ActionChunk
from uga.policy.chunk_controller import ActionChunkController
from uga.policy.fast_policy import FastPolicyOutput, PolicyContext
from uga.safety.focus_guard import AgentEnableState, FocusGuard
from uga.time.clock import ManualClock, UGATime


class FakeCaptureSource:
    def __init__(self, frames: FrameRingBuffer) -> None:
        self._frames = frames

    async def capture_once(self) -> SequencedFrame:
        return self._frames.publish(frame(1, timestamp_ns=100))


class FixedVisualCaptureSource:
    def __init__(self, frames: FrameRingBuffer, clock: ManualClock) -> None:
        self._frames = frames
        self._clock = clock
        self._number = 0
        self._base = frame(1, timestamp_ns=clock.now().value_ns)

    async def capture_once(self) -> SequencedFrame:
        self._number += 1
        fixed = replace(
            self._base,
            frame_id=f"frame-{self._number}",
            capture_timestamp=self._clock.now(),
        )
        return self._frames.publish(fixed)


class ForwardPolicy:
    def __init__(self, *, need_reasoning: bool = False) -> None:
        self._need_reasoning = need_reasoning

    @property
    def policy_version(self) -> str:
        return "fixture-policy"

    def infer(self, context: PolicyContext) -> FastPolicyOutput:
        chunk = ActionChunk(
            "chunk-1",
            context.observation_id,
            context.generated_at,
            context.generated_at,
            UGATime(context.generated_at.value_ns + 200_000_000),
            30.0,
            (0.0,) * 6,
            (1.0,) * 6,
            (0.0,) * 6,
            (0.0,) * 6,
            (0,) * 6,
            0.9,
            self.policy_version,
        )
        return FastPolicyOutput(
            chunk,
            self._need_reasoning,
            "low_confidence" if self._need_reasoning else None,
            5.0,
            5.0,
        )


class GroundedClickPlanner:
    def __init__(self) -> None:
        self.high_resolution_retries: list[bool] = []

    @property
    def policy_version(self) -> str:
        return "grounded-fixture"

    def decide(
        self,
        *,
        snapshot: PerceptionSnapshot,
        frames: tuple[Frame, ...],
        goal: str,
        high_resolution_retry: bool = False,
        preferred_action_available: bool = True,
    ) -> PlannerOutcome:
        del frames, goal, preferred_action_available
        self.high_resolution_retries.append(high_resolution_retry)
        return PlannerOutcome(
            f"grounded-decision-{snapshot.frame_sequence}",
            snapshot.frame_id,
            snapshot.frame_sequence,
            snapshot.window_identity.window_generation,
            snapshot.geometry_generation,
            snapshot.task_generation,
            DecisionKind.ACT,
            "fixture GUI",
            (),
            GoalStatus.IN_PROGRESS,
            0.95,
            GroundedAction(
                GuiActionKind.CLICK,
                "settings",
                NormalizedBox(0.25, 0.25, 0.75, 0.75),
                "settings opens",
                0.95,
            ),
        )
class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    def _build_loop(
        self, policy: ForwardPolicy
    ) -> tuple[RealtimeAgentLoop, DryRunInputBackend, EventBus]:
        clock = ManualClock(100)
        target = identity()
        frames = FrameRingBuffer()
        events = EventBus(clock)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        executor = InputExecutor(
            clock,
            backend,
            FocusGuard(FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)),
            leases,
        )
        scheduler = ActionScheduler(clock, executor, leases)
        environment = GenericEnvironment(profile())
        loop = RealtimeAgentLoop(
            clock=clock,
            capture=FakeCaptureSource(frames),
            frames=frames,
            observation_builder=ObservationBuilder(
                clock, "fixture-game", ObservationInputs("move forward")
            ),
            observations=TemporalObservationBuffer(),
            environment=environment,
            mode_classifier=RuleModeClassifier(),
            mode_router=ModeRouter(
                ControlMode.PLAY_3D, confirmation_frames=1, started_at=UGATime(0)
            ),
            policy=policy,
            leases=leases,
            controller=ActionChunkController(environment, ActionArbiter(clock, leases), scheduler),
            scheduler=scheduler,
            events=events,
        )
        return loop, backend, events

    async def test_capture_to_safe_input_closed_loop(self) -> None:
        loop, backend, events = self._build_loop(ForwardPolicy())

        result = await loop.step()

        self.assertEqual(result.observation.latest_frame.frame_id, "frame-1")
        self.assertIsNotNone(result.policy_output)
        self.assertIsNotNone(result.submission)
        self.assertEqual(result.scheduler_stats.executed, 1)
        self.assertEqual(len(backend.actions), 1)
        event_types = [event.event_type for event in events.history()]
        self.assertIn(EventType.OBSERVATION_BUILT, event_types)
        self.assertIn(EventType.LEASE_GRANTED, event_types)
        self.assertIn(EventType.ACTION_ACCEPTED, event_types)

    async def test_reasoning_request_cancels_chunk_without_input(self) -> None:
        loop, backend, events = self._build_loop(ForwardPolicy(need_reasoning=True))

        result = await loop.step()

        self.assertIsNotNone(result.policy_output)
        self.assertIsNone(result.submission)
        self.assertEqual(backend.actions, [])
        self.assertIn(
            EventType.REASONING_REQUESTED,
            [event.event_type for event in events.history()],
        )

    async def test_grounded_loop_executes_exactly_one_gui_action(self) -> None:
        clock = ManualClock(100)
        target = identity()
        frames = FrameRingBuffer()
        events = EventBus(clock)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        executor = InputExecutor(
            clock,
            backend,
            FocusGuard(FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)),
            leases,
        )
        scheduler = ActionScheduler(clock, executor, leases)
        arbiter = ActionArbiter(clock, leases)
        fixture_profile = profile()
        loop = RealtimeAgentLoop(
            clock=clock,
            capture=FakeCaptureSource(frames),
            frames=frames,
            observation_builder=ObservationBuilder(
                clock, "fixture-game", ObservationInputs("open settings")
            ),
            observations=TemporalObservationBuffer(),
            environment=GenericEnvironment(fixture_profile),
            mode_classifier=RuleModeClassifier(),
            mode_router=ModeRouter(
                ControlMode.GUI, confirmation_frames=1, started_at=UGATime(0)
            ),
            policy=None,
            leases=leases,
            controller=ActionChunkController(
                GenericEnvironment(fixture_profile), arbiter, scheduler
            ),
            scheduler=scheduler,
            events=events,
            grounded_planner=GroundedClickPlanner(),
            perception_builder=PerceptionBuilder(NullTextProvider()),
            closed_loop=ClosedLoopSupervisor(clock, fixture_profile.perception),
            gui_controller=GuiActionController(arbiter, scheduler),
            key_resolver=lambda _: None,
        )

        result = await loop.step()

        self.assertIsNotNone(result.supervision)
        assert result.supervision is not None
        self.assertEqual(result.supervision.disposition, DecisionDisposition.EXECUTE)
        self.assertIsNotNone(result.gui_submission)
        self.assertEqual(result.gui_submission.scheduled_physical_actions, 3)
        self.assertEqual(result.scheduler_stats.executed, 3)
        self.assertEqual(len(backend.actions), 3)

    async def test_grounded_loop_bounds_retries_then_executes_safe_back(self) -> None:
        clock = ManualClock(1)
        target = identity()
        frames = FrameRingBuffer()
        events = EventBus(clock)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        executor = InputExecutor(
            clock,
            backend,
            FocusGuard(FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)),
            leases,
        )
        scheduler = ActionScheduler(clock, executor, leases)
        arbiter = ActionArbiter(clock, leases)
        fixture_profile = replace(
            profile(),
            perception=PerceptionProfile(
                recovery_safe_actions=frozenset({"back"}),
                action_effect_timeout_ms=1000,
            ),
        )
        planner = GroundedClickPlanner()
        supervisor = ClosedLoopSupervisor(
            clock, fixture_profile.perception, max_recoveries=2
        )
        environment = GenericEnvironment(fixture_profile)
        loop = RealtimeAgentLoop(
            clock=clock,
            capture=FixedVisualCaptureSource(frames, clock),
            frames=frames,
            observation_builder=ObservationBuilder(
                clock, "fixture-game", ObservationInputs("open settings")
            ),
            observations=TemporalObservationBuffer(),
            environment=environment,
            mode_classifier=RuleModeClassifier(),
            mode_router=ModeRouter(
                ControlMode.GUI, confirmation_frames=1, started_at=UGATime(0)
            ),
            policy=None,
            leases=leases,
            controller=ActionChunkController(environment, arbiter, scheduler),
            scheduler=scheduler,
            events=events,
            grounded_planner=planner,
            perception_builder=PerceptionBuilder(NullTextProvider()),
            closed_loop=supervisor,
            gui_controller=GuiActionController(arbiter, scheduler),
            key_resolver=lambda name: (27,) if name == "back" else None,
        )

        first = await loop.step()
        clock.advance(1_000_000_000)
        second = await loop.step()
        clock.advance(1_000_000_000)
        recovered = await loop.step()

        self.assertEqual(first.supervision.disposition, DecisionDisposition.EXECUTE)  # type: ignore[union-attr]
        self.assertEqual(second.supervision.disposition, DecisionDisposition.EXECUTE)  # type: ignore[union-attr]
        self.assertEqual(recovered.supervision.disposition, DecisionDisposition.RECOVER)  # type: ignore[union-attr]
        self.assertEqual(planner.high_resolution_retries, [False, False, True])
        self.assertEqual(supervisor.recovery_count, 2)
        self.assertEqual(len(backend.actions), 8)


if __name__ == "__main__":
    unittest.main()
