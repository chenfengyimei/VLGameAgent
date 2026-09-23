from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace

from tests.helpers import frame, identity
from tests.integration.test_baseline_agent import FakeIntegrity, FakeWindows, profile
from uga.agent.closed_loop import ClosedLoopSupervisor, DecisionDisposition
from uga.agent.mode_router import ModeRouter, RuleModeClassifier
from uga.agent.session_state import GameSessionState
from uga.capture.frame import Frame
from uga.capture.ring_buffer import FrameRingBuffer, SequencedFrame
from uga.control.arbiter import ActionArbiter
from uga.control.executor import InputExecutor
from uga.control.input_backend import DryRunInputBackend
from uga.control.lease import ControlMode
from uga.control.lease_manager import ControlLeaseManager
from uga.control.physical import AbsolutePointerAction
from uga.control.scheduler import ActionScheduler
from uga.core.agent_loop import RealtimeAgentLoop
from uga.core.errors import BackendUnavailableError
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
    TextRegion,
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


class MutableSignaturePerceptionBuilder:
    def __init__(self, signature: str) -> None:
        self.signature = signature

    def build(
        self,
        item: SequencedFrame,
        mode: ControlMode,
        *,
        geometry_generation: int,
        task_generation: int,
        goal_facts: tuple[tuple[str, str], ...] = (),
    ) -> PerceptionSnapshot:
        return PerceptionSnapshot(
            f"snapshot-{item.sequence}",
            item.frame.frame_id,
            item.sequence,
            item.frame.capture_timestamp,
            item.frame.window_identity,
            geometry_generation,
            task_generation,
            mode,
            (),
            (),
            goal_facts,
            self.signature,
            1.0,
        )


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
        session_context: str | None = None,
        quest_target_level: int | None = None,
        quest_text: str | None = None,
        restored_task_unverified: bool = False,
    ) -> PlannerOutcome:
        del frames, goal, preferred_action_available, session_context
        del quest_target_level, quest_text, restored_task_unverified
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


class DialogueClickPlanner(GroundedClickPlanner):
    last_decision_source = "ocr_dialogue_click_fast"

    def decide(self, **kwargs: object) -> PlannerOutcome:
        outcome = super().decide(**kwargs)
        return replace(
            outcome,
            action=GroundedAction(
                GuiActionKind.CLICK,
                "ui_dialogue_advance",
                NormalizedBox(0.94, 0.89, 0.98, 0.94),
                "the dialogue advances",
                1.0,
            ),
        )


class DialogueTextProvider:
    @property
    def available(self) -> bool:
        return True

    def recognize(self, source: Frame) -> tuple[TextRegion, ...]:
        del source
        return (
            TextRegion("回顾剧情", NormalizedBox(0.02, 0.30, 0.10, 0.60), 0.99),
        )


class UnavailableGroundedPlanner(GroundedClickPlanner):
    def decide(self, **kwargs: object) -> PlannerOutcome:
        del kwargs
        raise BackendUnavailableError("fixture planner timeout")


class StripCloseClickPlanner(GroundedClickPlanner):
    """Exit-labelled click whose box lands on MuMu's title-bar close button."""

    def decide(self, **kwargs: object) -> PlannerOutcome:
        snapshot = kwargs["snapshot"]
        assert isinstance(snapshot, PerceptionSnapshot)
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
                "返回花纹",
                NormalizedBox(0.972, 0.012, 0.99, 0.04),
                "the page closes",
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

    async def test_dialogue_burst_submits_without_another_planner_pass(self) -> None:
        clock = ManualClock(100)
        target = identity()
        frames = FrameRingBuffer()
        events = EventBus(clock)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        scheduler = ActionScheduler(
            clock,
            InputExecutor(
                clock,
                backend,
                FocusGuard(
                    FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)
                ),
                leases,
            ),
            leases,
        )
        arbiter = ActionArbiter(clock, leases)
        fixture_profile = profile()
        session = GameSessionState()
        loop = RealtimeAgentLoop(
            clock=clock,
            capture=FakeCaptureSource(frames),
            frames=frames,
            observation_builder=ObservationBuilder(
                clock, "fixture-game", ObservationInputs("advance dialogue")
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
            grounded_planner=DialogueClickPlanner(),
            perception_builder=PerceptionBuilder(DialogueTextProvider()),
            closed_loop=ClosedLoopSupervisor(
                clock, fixture_profile.perception, session=session
            ),
            gui_controller=GuiActionController(arbiter, scheduler),
            key_resolver=lambda _: None,
        )

        first = await loop.step()
        self.assertIsNotNone(first.gui_submission)
        self.assertEqual(len(backend.actions), 3)
        diagnostics = loop.closed_loop_diagnostics
        assert diagnostics is not None
        self.assertTrue(diagnostics["dialogue_burst_active"])

        clock.advance(250_000_000)
        permit = loop._dialogue_burst.take_due(clock.now())  # noqa: SLF001
        assert permit is not None
        await loop._submit_dialogue_burst(permit)  # noqa: SLF001

        self.assertEqual(len(backend.actions), 6)
        diagnostics = loop.closed_loop_diagnostics
        assert diagnostics is not None
        self.assertEqual(diagnostics["dialogue_burst_executed"], 1)

    async def test_execution_receipts_feed_effect_verification(self) -> None:
        # D04: arbiter acceptance only queues work.  The pending action's
        # effect clock may only start once the primitives actually executed —
        # observable here because a working receipt pump routes the timed-out
        # action into the ineffective path, while a broken pump would route it
        # into not_executed.
        clock = ManualClock(100)
        target = identity()
        frames = FrameRingBuffer()
        events = EventBus(clock)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        scheduler = ActionScheduler(
            clock,
            InputExecutor(
                clock,
                backend,
                FocusGuard(
                    FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)
                ),
                leases,
            ),
            leases,
        )
        arbiter = ActionArbiter(clock, leases)
        fixture_profile = profile()
        loop = RealtimeAgentLoop(
            clock=clock,
            capture=FixedVisualCaptureSource(frames, clock),
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

        first = await loop.step()
        assert first.gui_submission is not None
        self.assertEqual(first.scheduler_stats.executed, 3)
        self.assertEqual(len(backend.actions), 3)

        clock.set(100 + 4_000_000_000)
        second = await loop.step()
        del second
        diagnostics = loop.closed_loop_diagnostics
        assert diagnostics is not None
        # The receipt pump fed the pending action before observe() ran, so
        # the effect clock ran (and timed out into the ineffective path) —
        # never the not_executed path.
        self.assertEqual(diagnostics["ineffective_actions"], 1)
        self.assertEqual(diagnostics["not_executed_actions"], 0)

    async def test_continuous_budget_exhaustion_stops_the_rebuild_loop(self) -> None:
        # F06: a blocked cycle on an exhausted recovery budget must end the
        # run — rebuilding would spin blocked supervisors at observation rate.
        from uga.agent.recovery_budget import RecoveryBudget

        clock = ManualClock(100)
        target = identity()
        frames = FrameRingBuffer()
        events = EventBus(clock)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        scheduler = ActionScheduler(
            clock,
            InputExecutor(
                clock,
                backend,
                FocusGuard(
                    FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)
                ),
                leases,
            ),
            leases,
        )
        arbiter = ActionArbiter(clock, leases)
        fixture_profile = profile()
        budget = RecoveryBudget(0)
        factory_calls: list[int] = []

        def make_supervisor() -> ClosedLoopSupervisor:
            factory_calls.append(1)
            supervisor = ClosedLoopSupervisor(clock, fixture_profile.perception)
            supervisor.fail("fixture blocked")
            return supervisor

        loop = RealtimeAgentLoop(
            clock=clock,
            capture=FakeCaptureSource(frames),
            frames=frames,
            observation_builder=ObservationBuilder(
                clock, "fixture-game", ObservationInputs("keep progressing")
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
            closed_loop=make_supervisor(),
            gui_controller=GuiActionController(arbiter, scheduler),
            key_resolver=lambda _: None,
            continuous_grounded=True,
            closed_loop_factory=make_supervisor,
            recovery_budget=budget,
        )

        stop = asyncio.Event()
        task = asyncio.create_task(loop.run(stop, observation_hz=100.0))
        await asyncio.wait_for(task, timeout=5.0)

        # The exhausted budget refused the rebuild: no fresh supervisor, no
        # reset accounting.
        self.assertEqual(factory_calls, [1])
        self.assertTrue(budget.exhausted)

    async def test_continuous_rebuilds_are_bounded(self) -> None:
        # F06: continuous rebuilds may start new task cycles, but a run that
        # keeps failing is bounded by an explicit rebuild cap.
        from uga.agent.recovery_budget import RecoveryBudget

        clock = ManualClock(100)
        target = identity()
        frames = FrameRingBuffer()
        events = EventBus(clock)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        scheduler = ActionScheduler(
            clock,
            InputExecutor(
                clock,
                backend,
                FocusGuard(
                    FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)
                ),
                leases,
            ),
            leases,
        )
        arbiter = ActionArbiter(clock, leases)
        fixture_profile = profile()
        budget = RecoveryBudget(2)
        factory_calls: list[int] = []

        def make_supervisor() -> ClosedLoopSupervisor:
            factory_calls.append(1)
            supervisor = ClosedLoopSupervisor(clock, fixture_profile.perception)
            supervisor.fail("fixture blocked")
            return supervisor

        loop = RealtimeAgentLoop(
            clock=clock,
            capture=FakeCaptureSource(frames),
            frames=frames,
            observation_builder=ObservationBuilder(
                clock, "fixture-game", ObservationInputs("keep progressing")
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
            closed_loop=make_supervisor(),
            gui_controller=GuiActionController(arbiter, scheduler),
            key_resolver=lambda _: None,
            continuous_grounded=True,
            closed_loop_factory=make_supervisor,
            recovery_budget=budget,
            max_continuous_rebuilds=3,
        )

        stop = asyncio.Event()
        task = asyncio.create_task(loop.run(stop, observation_hz=100.0))
        await asyncio.wait_for(task, timeout=5.0)

        # Initial supervisor + 3 bounded rebuilds, then an honest stop.
        self.assertEqual(len(factory_calls), 4)

    async def test_routed_exit_intent_submits_the_calibrated_hotspot(self) -> None:
        # 回归：assess() 的出口路由会替换 proposed action（返回语义 → 校准
        # 热点），但提交层曾用原始 planner outcome——模型把"返回花纹"的框
        # 落在 MuMu 标题栏 ✕ 上时，原始坐标直达 SendInput 关掉了模拟器。
        clock = ManualClock(100)
        target = identity()
        frames = FrameRingBuffer()
        events = EventBus(clock)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        scheduler = ActionScheduler(
            clock,
            InputExecutor(
                clock,
                backend,
                FocusGuard(
                    FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)
                ),
                leases,
            ),
            leases,
        )
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
            grounded_planner=StripCloseClickPlanner(),
            perception_builder=PerceptionBuilder(NullTextProvider()),
            closed_loop=ClosedLoopSupervisor(
                clock, fixture_profile.perception, back_hotspot=(0.06, 0.08)
            ),
            gui_controller=GuiActionController(arbiter, scheduler),
            key_resolver=lambda _: None,
        )

        result = await loop.step()

        assert result.supervision is not None
        self.assertEqual(result.supervision.disposition, DecisionDisposition.EXECUTE)
        assert result.gui_submission is not None
        pointer = next(
            action
            for action in result.gui_submission.physical_actions
            if isinstance(action, AbsolutePointerAction)
        )
        # 客户区宽 2px、physical_rect.left=-100：校准热点 x=0.06 → 物理
        # x≈-100；原始 ✕ 框 x=0.981 → ≈-98。
        self.assertEqual(pointer.x, -100)

    async def test_grounded_planner_unavailable_is_retried_without_stopping(self) -> None:
        clock = ManualClock(100)
        target = identity()
        frames = FrameRingBuffer()
        events = EventBus(clock)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        scheduler = ActionScheduler(
            clock,
            InputExecutor(
                clock,
                backend,
                FocusGuard(
                    FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)
                ),
                leases,
            ),
            leases,
        )
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
                GenericEnvironment(fixture_profile), ActionArbiter(clock, leases), scheduler
            ),
            scheduler=scheduler,
            events=events,
            grounded_planner=UnavailableGroundedPlanner(),
            perception_builder=PerceptionBuilder(NullTextProvider()),
            closed_loop=ClosedLoopSupervisor(clock, fixture_profile.perception),
            gui_controller=GuiActionController(ActionArbiter(clock, leases), scheduler),
            key_resolver=lambda _: None,
        )

        result = await loop.step()

        self.assertIsNone(result.planner_outcome)
        self.assertEqual(loop.terminal_status.value, "running")
        self.assertEqual(loop.closed_loop_diagnostics["planner_failure_count"], 1)  # type: ignore[index]
        self.assertIn(
            "fixture planner timeout",
            str(loop.closed_loop_diagnostics["last_planner_error"]),  # type: ignore[index]
        )

    async def test_continuous_grounded_mode_resets_terminal_cycle(self) -> None:
        clock = ManualClock(100)
        target = identity()
        frames = FrameRingBuffer()
        events = EventBus(clock)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        scheduler = ActionScheduler(
            clock,
            InputExecutor(
                clock,
                backend,
                FocusGuard(
                    FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)
                ),
                leases,
            ),
            leases,
        )
        fixture_profile = profile()

        def make_supervisor() -> ClosedLoopSupervisor:
            return ClosedLoopSupervisor(clock, fixture_profile.perception)

        loop = RealtimeAgentLoop(
            clock=clock,
            capture=FakeCaptureSource(frames),
            frames=frames,
            observation_builder=ObservationBuilder(
                clock, "fixture-game", ObservationInputs("keep progressing")
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
                GenericEnvironment(fixture_profile), ActionArbiter(clock, leases), scheduler
            ),
            scheduler=scheduler,
            events=events,
            grounded_planner=GroundedClickPlanner(),
            perception_builder=PerceptionBuilder(NullTextProvider()),
            closed_loop=make_supervisor(),
            gui_controller=GuiActionController(ActionArbiter(clock, leases), scheduler),
            key_resolver=lambda _: None,
            continuous_grounded=True,
            closed_loop_factory=make_supervisor,
        )
        loop.fail_closed_loop("fixture blocked")
        stop = asyncio.Event()
        task = asyncio.create_task(loop.run(stop, observation_hz=100.0))

        for _ in range(20):
            diagnostics = loop.closed_loop_diagnostics
            if diagnostics is not None and diagnostics["continuous_cycle_count"] >= 2:
                break
            await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        diagnostics = loop.closed_loop_diagnostics
        self.assertIsNotNone(diagnostics)
        self.assertGreaterEqual(diagnostics["continuous_cycle_count"], 2)  # type: ignore[index]
        self.assertEqual(loop.terminal_status.value, "running")
        stop.set()
        await task

    async def test_continuous_blocked_mode_waits_until_screen_changes(self) -> None:
        clock = ManualClock(100)
        target = identity()
        frames = FrameRingBuffer()
        events = EventBus(clock)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        scheduler = ActionScheduler(
            clock,
            InputExecutor(
                clock,
                backend,
                FocusGuard(
                    FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)
                ),
                leases,
            ),
            leases,
        )
        fixture_profile = profile()
        factory_calls: list[int] = []

        def make_supervisor() -> ClosedLoopSupervisor:
            factory_calls.append(len(factory_calls) + 1)
            return ClosedLoopSupervisor(clock, fixture_profile.perception)

        supervisor = make_supervisor()
        supervisor._stop_blocked("fixture refusal")  # noqa: SLF001
        perception_builder = MutableSignaturePerceptionBuilder("screen-a")
        loop = RealtimeAgentLoop(
            clock=clock,
            capture=FakeCaptureSource(frames),
            frames=frames,
            observation_builder=ObservationBuilder(
                clock, "fixture-game", ObservationInputs("keep progressing")
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
                GenericEnvironment(fixture_profile), ActionArbiter(clock, leases), scheduler
            ),
            scheduler=scheduler,
            events=events,
            grounded_planner=GroundedClickPlanner(),
            perception_builder=perception_builder,  # type: ignore[arg-type]
            closed_loop=supervisor,
            gui_controller=GuiActionController(ActionArbiter(clock, leases), scheduler),
            key_resolver=lambda _: None,
            continuous_grounded=True,
            closed_loop_factory=make_supervisor,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(loop.run(stop, observation_hz=100.0))

        await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        self.assertEqual(factory_calls, [1])
        waiting = [
            event
            for event in events.history()
            if event.event_type is EventType.AGENT_STUCK
            and event.payload.get("disposition") == "wait_for_state_change"
        ]
        self.assertEqual(len(waiting), 1)

        perception_builder.signature = "screen-b"
        for _ in range(20):
            if len(factory_calls) == 2:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(factory_calls, [1, 2])
        self.assertFalse(task.done())
        self.assertEqual(loop.terminal_status.value, "running")

        stop.set()
        await task

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
        # The visual/key back recovery is an ordinary state transition and no
        # longer consumes the second recovery slot.
        self.assertEqual(supervisor.recovery_count, 1)
        self.assertEqual(len(backend.actions), 8)


if __name__ == "__main__":
    unittest.main()
