"""Latched emergency-stop regression tests for the real composition paths.

Covers the safety chain the 2026-09-16 audit flagged as F01: a stop that lands
while a decision is in flight must never allow the late result to reach the
physical input queue, the executor must refuse writes once the latch is set,
and continuous-mode supervisor rebuilds must never clear a latched safety trip.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from collections.abc import Callable
from threading import Event, Thread

from tests.helpers import identity
from tests.integration.test_agent_loop import (
    FakeCaptureSource,
    ForwardPolicy,
    GroundedClickPlanner,
)
from tests.integration.test_baseline_agent import FakeIntegrity, FakeWindows, profile
from uga.agent.closed_loop import ClosedLoopSupervisor
from uga.agent.mode_router import ModeRouter, RuleModeClassifier
from uga.capture.ring_buffer import FrameRingBuffer
from uga.control.arbiter import ActionArbiter
from uga.control.executor import InputExecutor
from uga.control.input_backend import DryRunInputBackend
from uga.control.lease import ControlMode, ControlOwner
from uga.control.lease_manager import ControlLeaseManager
from uga.control.lifetime import ActionLifetime
from uga.control.physical import KeyboardAction
from uga.control.scheduler import ActionScheduler
from uga.core.agent_loop import RealtimeAgentLoop
from uga.core.events import EventBus
from uga.core.run_context import RunContext
from uga.environment.generic import GenericEnvironment
from uga.gui.controller import GuiActionController
from uga.observation.buffer import TemporalObservationBuffer
from uga.observation.builder import ObservationBuilder, ObservationInputs
from uga.perception.builder import PerceptionBuilder
from uga.perception.text import NullTextProvider
from uga.policy.chunk_controller import ActionChunkController
from uga.safety.emergency_stop import EmergencyStop
from uga.safety.focus_guard import AgentEnableState, FocusGuard, GuardReason
from uga.safety.shutdown import SafetyShutdown, ShutdownCause
from uga.time.clock import ManualClock, UGATime


class CancelingGroundedPlanner(GroundedClickPlanner):
    """The stop lands while the model result is in flight: result arrives late."""

    def __init__(self, run_context: RunContext) -> None:
        super().__init__()
        self._run_context = run_context

    def decide(self, **kwargs: object) -> object:
        outcome = super().decide(**kwargs)
        self._run_context.cancel()
        return outcome


class CancelingSupervisor(ClosedLoopSupervisor):
    """The stop lands while supervision runs: assess returns, nothing enqueues."""

    def __init__(self, *args: object, run_context: RunContext, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._run_context = run_context

    def assess(self, *args: object, **kwargs: object) -> object:
        supervised = super().assess(*args, **kwargs)
        self._run_context.cancel()
        return supervised


class CancelingForwardPolicy(ForwardPolicy):
    """Fast-policy late result: the run dies while inference is in flight."""

    def __init__(self, run_context: RunContext) -> None:
        super().__init__()
        self._run_context = run_context

    def infer(self, context: object) -> object:
        output = super().infer(context)
        self._run_context.cancel()
        return output


class BlockingSubmitBackend(DryRunInputBackend):
    """Stall one input write mid-flight so the hotkey races it from a thread."""

    def __init__(self) -> None:
        super().__init__()
        self.submit_started = Event()
        self.allow_submit = Event()

    def submit(self, action: object) -> bool:
        self.submit_started.set()
        assert self.allow_submit.wait(timeout=5.0)
        return super().submit(action)  # type: ignore[arg-type]


class LatchedShutdownTests(unittest.IsolatedAsyncioTestCase):
    def _grounded_loop(
        self,
        *,
        planner_factory: Callable[[], object],
        supervisor_factory: Callable[[ManualClock], ClosedLoopSupervisor],
        run_context: RunContext | None = None,
        continuous: bool = False,
        closed_loop_factory: Callable[[], ClosedLoopSupervisor] | None = None,
    ) -> tuple[RealtimeAgentLoop, DryRunInputBackend]:
        clock = ManualClock(100)
        target = identity()
        frames = FrameRingBuffer()
        events = EventBus(clock)
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        guard = FocusGuard(
            FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)
        )
        executor = InputExecutor(clock, backend, guard, leases)
        scheduler = ActionScheduler(clock, executor, leases)
        arbiter = ActionArbiter(clock, leases)
        fixture_profile = profile()
        environment = GenericEnvironment(fixture_profile)
        loop = RealtimeAgentLoop(
            clock=clock,
            capture=FakeCaptureSource(frames),
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
            grounded_planner=planner_factory(),
            perception_builder=PerceptionBuilder(NullTextProvider()),
            closed_loop=supervisor_factory(clock),
            gui_controller=GuiActionController(arbiter, scheduler),
            key_resolver=lambda _: None,
            continuous_grounded=continuous,
            closed_loop_factory=closed_loop_factory,
            run_context=run_context,
        )
        return loop, backend

    async def test_stop_during_planning_cannot_submit_late_action(self) -> None:
        run_context = RunContext()

        def make_supervisor(clock: ManualClock) -> ClosedLoopSupervisor:
            return ClosedLoopSupervisor(clock, profile().perception)

        loop, backend = self._grounded_loop(
            planner_factory=lambda: CancelingGroundedPlanner(run_context),
            supervisor_factory=make_supervisor,
            run_context=run_context,
        )

        result = await loop.step()

        self.assertIsNone(result.gui_submission)
        self.assertEqual(backend.actions, [])
        diagnostics = loop.closed_loop_diagnostics
        assert diagnostics is not None
        self.assertEqual(diagnostics["safety_discarded_results"], 1)

    async def test_stop_between_assess_and_enqueue(self) -> None:
        run_context = RunContext()

        def make_supervisor(clock: ManualClock) -> ClosedLoopSupervisor:
            return CancelingSupervisor(clock, profile().perception, run_context=run_context)

        loop, backend = self._grounded_loop(
            planner_factory=GroundedClickPlanner,
            supervisor_factory=make_supervisor,
            run_context=run_context,
        )

        result = await loop.step()

        self.assertIsNone(result.gui_submission)
        self.assertEqual(backend.actions, [])
        diagnostics = loop.closed_loop_diagnostics
        assert diagnostics is not None
        self.assertEqual(diagnostics["safety_discarded_results"], 1)

    async def test_late_fast_policy_result_is_discarded(self) -> None:
        clock = ManualClock(100)
        target = identity()
        frames = FrameRingBuffer()
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        run_context = RunContext()
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
            policy=CancelingForwardPolicy(run_context),
            leases=leases,
            controller=ActionChunkController(
                environment, ActionArbiter(clock, leases), ActionScheduler(
                    clock,
                    InputExecutor(
                        clock,
                        backend,
                        FocusGuard(
                            FakeWindows(target), FakeIntegrity(), leases,
                            AgentEnableState(True),
                        ),
                        leases,
                    ),
                    leases,
                )
            ),
            scheduler=ActionScheduler(
                clock,
                InputExecutor(
                    clock,
                    backend,
                    FocusGuard(
                        FakeWindows(target), FakeIntegrity(), leases,
                        AgentEnableState(True),
                    ),
                    leases,
                ),
                leases,
            ),
            events=EventBus(clock),
            run_context=run_context,
        )

        result = await loop.step()

        self.assertIsNone(result.submission)
        self.assertEqual(backend.actions, [])

    async def test_continuous_cannot_clear_safety_trip(self) -> None:
        clock = ManualClock(100)
        target = identity()
        frames = FrameRingBuffer()
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        enabled = AgentEnableState(True)
        guard = FocusGuard(FakeWindows(target), FakeIntegrity(), leases, enabled)
        executor = InputExecutor(clock, backend, guard, leases)
        scheduler = ActionScheduler(clock, executor, leases)
        safety = SafetyShutdown(clock, leases, scheduler, executor, enabled)
        run_context = RunContext(safety)

        def make_supervisor() -> ClosedLoopSupervisor:
            return ClosedLoopSupervisor(clock, profile().perception)

        loop = RealtimeAgentLoop(
            clock=clock,
            capture=FakeCaptureSource(frames),
            frames=frames,
            observation_builder=ObservationBuilder(
                clock, "fixture-game", ObservationInputs("open settings")
            ),
            observations=TemporalObservationBuffer(),
            environment=GenericEnvironment(profile()),
            mode_classifier=RuleModeClassifier(),
            mode_router=ModeRouter(
                ControlMode.GUI, confirmation_frames=1, started_at=UGATime(0)
            ),
            policy=None,
            leases=leases,
            controller=ActionChunkController(
                GenericEnvironment(profile()),
                ActionArbiter(clock, leases),
                scheduler,
            ),
            scheduler=scheduler,
            events=EventBus(clock),
            grounded_planner=GroundedClickPlanner(),
            perception_builder=PerceptionBuilder(NullTextProvider()),
            closed_loop=make_supervisor(),
            gui_controller=GuiActionController(ActionArbiter(clock, leases), scheduler),
            key_resolver=lambda _: None,
            continuous_grounded=True,
            closed_loop_factory=make_supervisor,
            run_context=run_context,
        )
        EmergencyStop(safety).trigger()
        self.assertFalse(enabled.get())
        loop.fail_closed_loop("fixture blocked")

        stop = asyncio.Event()
        task = asyncio.create_task(loop.run(stop, observation_hz=100.0))
        await asyncio.wait_for(task, timeout=5.0)

        # The trip latches: no rebuild, no re-arm, no input.
        self.assertFalse(run_context.is_live(run_context.stamp()))
        diagnostics = loop.closed_loop_diagnostics
        assert diagnostics is not None
        self.assertEqual(diagnostics["continuous_cycle_count"], 1)
        self.assertEqual(backend.actions, [])
        self.assertFalse(enabled.get())

    async def test_scheduler_run_publishes_control_heartbeats(self) -> None:
        clock = ManualClock(100)
        target = identity()
        leases = ControlLeaseManager(clock)
        backend = DryRunInputBackend()
        guard = FocusGuard(
            FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)
        )
        executor = InputExecutor(clock, backend, guard, leases)
        scheduler = ActionScheduler(clock, executor, leases)
        beats: list[int] = []
        stop = asyncio.Event()

        async def halt() -> None:
            await asyncio.sleep(0.05)
            stop.set()

        await asyncio.gather(
            scheduler.run(stop, frequency_hz=100.0, heartbeat=lambda: beats.append(1)),
            halt(),
        )
        # Heartbeats prove the control plane kept draining; they must ride
        # actual scheduler ticks, not a blind timer.
        self.assertGreaterEqual(len(beats), 1)

    def test_no_new_submit_after_the_latch_confirms(self) -> None:
        clock = ManualClock(100)
        target = identity()
        backend = BlockingSubmitBackend()
        enabled = AgentEnableState(True)
        leases = ControlLeaseManager(clock)
        guard = FocusGuard(FakeWindows(target), FakeIntegrity(), leases, enabled)
        executor = InputExecutor(clock, backend, guard, leases)
        scheduler = ActionScheduler(clock, executor, leases)
        shutdown = SafetyShutdown(clock, leases, scheduler, executor, enabled)
        lease = leases.grant(
            ControlOwner.FAST_POLICY,
            ControlMode.PLAY_3D,
            1_000,
            confidence=0.9,
            reason="test",
        )
        lifetime = ActionLifetime(UGATime(100), UGATime(100), UGATime(500))
        first = KeyboardAction("key-down", lifetime, 0x11, True)
        second = KeyboardAction("key-down-2", lifetime, 0x11, True)

        in_flight = Thread(target=lambda: executor.execute(first, target, lease))
        in_flight.start()
        self.assertTrue(backend.submit_started.wait(timeout=1.0))

        # The emergency hotkey fires while one write is already mid-flight.
        hotkey = Thread(
            target=lambda: shutdown.trip(ShutdownCause.EMERGENCY_HOTKEY)
        )
        hotkey.start()
        deadline = time.monotonic() + 2.0
        while enabled.get() and time.monotonic() < deadline:
            time.sleep(0.001)
        # From the moment the latch starts (inputs disabled first), a new
        # write attempt is refused before it can reach the backend.
        blocked = executor.execute(second, target, lease)
        self.assertFalse(blocked.executed)
        self.assertEqual(blocked.reason, GuardReason.AGENT_DISABLED)

        # Let the historical in-flight write finish; the latch then completes
        # its ordered revoke/flush/release. No new submission may follow.
        backend.allow_submit.set()
        in_flight.join(timeout=5.0)
        hotkey.join(timeout=5.0)
        self.assertFalse(in_flight.is_alive())
        self.assertFalse(hotkey.is_alive())
        self.assertIsNotNone(shutdown.tripped)
        after_trip = executor.execute(second, target, lease)
        self.assertFalse(after_trip.executed)
        self.assertEqual(after_trip.reason, GuardReason.AGENT_DISABLED)
        self.assertEqual(len(backend.actions), 1)
        self.assertGreaterEqual(backend.release_count, 1)


if __name__ == "__main__":
    unittest.main()
