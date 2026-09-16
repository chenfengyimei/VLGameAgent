from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import frame, identity
from uga.agent.belief import BeliefState
from uga.agent.memory import MemoryKind, MemoryStore
from uga.agent.mode_router import ModeEvidence, ModeRouter
from uga.agent.planner import PlannerRequest, QwenVlmPlannerBackend
from uga.agent.recovery import (
    FailureKind,
    FailureReport,
    RecoveryStrategy,
    ReflectionEngine,
    ReflectionTrigger,
)
from uga.agent.skills import SkillRegistry
from uga.agent.task_graph import RetryPolicy, TaskGraph, TaskNode, TaskStatus
from uga.agent.vertical_slice import (
    FirstAvailableSkillPlanner,
    FirstVerticalSlice,
    MoveForwardSkill,
)
from uga.control.arbiter import ActionArbiter
from uga.control.canonical import CanonicalAction
from uga.control.executor import InputExecutor
from uga.control.input_backend import DryRunInputBackend
from uga.control.lease import ControlMode, ControlOwner
from uga.control.lease_manager import ControlLeaseManager
from uga.control.lifetime import ActionLifetime
from uga.control.physical import MouseButtonAction, UnicodeTextAction
from uga.control.scheduler import ActionScheduler
from uga.core.errors import ContractViolation
from uga.environment.generic import GenericEnvironment
from uga.environment.profile import (
    BindingKind,
    ControlBinding,
    EnvironmentCapabilityLevel,
    GameCapabilities,
    GameProfile,
    load_game_profile,
)
from uga.gui.agent import UiTarsBackend
from uga.gui.control_bridge import GuiControlBridge
from uga.gui.schema import GuiAction, GuiActionKind, GuiResult, GuiTask
from uga.models.registry import ModelRole, load_model_registry
from uga.observation.buffer import ObservationRepresentation, TemporalObservationBuffer
from uga.observation.schema import LatencyContext, Observation
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.replay import ReplayEngine
from uga.recording.schema import EpisodeMetadata, EpisodeResult
from uga.recording.video import PyAvVideoRecorder
from uga.safety.focus_guard import AgentEnableState, FocusGuard
from uga.time.clock import ManualClock, UGATime
from uga.windows.backend import WindowSnapshot
from uga.windows.coordinates import CoordinateSpace, CoordinateTransform, Rect
from uga.windows.integrity import IntegrityLevel
from uga.windows.window_identity import WindowIdentity


def profile() -> GameProfile:
    return GameProfile(
        "fixture-game",
        "Fixture Game",
        ("fixture.exe",),
        "auto",
        (
            ControlBinding("move_forward", BindingKind.SCAN_CODE, 0x11, True),
            ControlBinding("menu", BindingKind.VIRTUAL_KEY, 0x1B, True),
        ),
        "relative_mouse",
        100.0,
        GameCapabilities(True, True, False, False),
        EnvironmentCapabilityLevel.USER_CONFIRMED_PROFILE,
    )


def observation(
    created_ns: int = 200,
    mode: ControlMode = ControlMode.PLAY_3D,
    visible_text: tuple[str, ...] = (),
) -> Observation:
    latest = frame(created_ns, timestamp_ns=created_ns - 1)
    belief = BeliefState(
        "fixture-game",
        mode,
        "move forward",
        "reach marker",
        (),
        (),
        (),
        (),
        (),
        (),
        (("progress", "unknown"),),
        None,
        None,
        0.8,
        UGATime(created_ns),
    )
    return Observation(
        f"observation-{created_ns}",
        UGATime(created_ns),
        latest,
        (latest,),
        mode,
        "move forward",
        "reach marker",
        (),
        (),
        belief,
        visible_text,
        None,
        None,
        LatencyContext(1, 1, 100_000_000),
    )


class FakeWindows:
    def __init__(self, target: WindowIdentity) -> None:
        self.target = target

    def discover(self, *, executable_name: str | None = None) -> tuple[WindowSnapshot, ...]:
        del executable_name
        return (self.snapshot(self.target.hwnd),)

    def snapshot(self, hwnd: int) -> WindowSnapshot:
        if hwnd != self.target.hwnd:
            raise LookupError(hwnd)
        return WindowSnapshot(
            self.target,
            "fixture",
            Rect(0, 0, 2, 2),
            Rect(0, 0, 2, 2),
            96,
            True,
            True,
        )

    def foreground_hwnd(self) -> int | None:
        return self.target.hwnd


class FakeIntegrity:
    def current_process(self) -> IntegrityLevel:
        return IntegrityLevel.MEDIUM

    def process(self, pid: int) -> IntegrityLevel:
        del pid
        return IntegrityLevel.MEDIUM


class FakeJsonModel:
    model_version = "qwen-fixture"

    def generate_json(self, *, instruction: str, observation: Observation) -> str:
        self.last_instruction = instruction
        del observation
        return (
            '{"subgoal":"reach marker","mode":"PLAY_3D","skill":"move_forward",'
            '"target":"marker","success_condition":"marker reached",'
            '"failure_condition":"no progress"}'
        )


class FakeGuiModel:
    model_version = "ui-tars-fixture"

    def generate_actions(self, task: GuiTask, observation: Observation) -> GuiResult:
        lifetime = ActionLifetime(
            observation.created_at,
            observation.created_at,
            UGATime(observation.created_at.value_ns + 100),
        )
        return GuiResult(
            task.task_id,
            (GuiAction("click", GuiActionKind.CLICK, lifetime, 0.5, 0.5),),
            False,
            "click center",
        )


class BaselineAgentTests(unittest.TestCase):
    def test_temporal_buffer_uses_mixed_representations(self) -> None:
        buffer = TemporalObservationBuffer(5, low_resolution_count=2)
        for timestamp in range(200, 205):
            buffer.append(observation(timestamp))
        representations = [item.representation for item in buffer.context()]
        self.assertEqual(representations[0], ObservationRepresentation.SUMMARY)
        self.assertEqual(representations[-1], ObservationRepresentation.HIGH_RESOLUTION)
        self.assertEqual(representations.count(ObservationRepresentation.LOW_RESOLUTION), 2)
        envelope = buffer.latest().to_envelope()  # type: ignore[union-attr]
        serialized = json.dumps(envelope)
        self.assertNotIn("payload", serialized)
        self.assertIn("buffer_handle", serialized)

    def test_mode_router_requires_confidence_count_and_hold_time(self) -> None:
        router = ModeRouter(
            ControlMode.PLAY_3D,
            confirmation_frames=3,
            minimum_hold_ns=500,
            transition_confidence=0.8,
            started_at=UGATime(0),
        )
        self.assertIsNone(router.consider(ModeEvidence(ControlMode.GUI, 0.9, "test", UGATime(500))))
        self.assertIsNone(router.consider(ModeEvidence(ControlMode.GUI, 0.9, "test", UGATime(600))))
        transition = router.consider(ModeEvidence(ControlMode.GUI, 0.9, "test", UGATime(700)))
        self.assertEqual(transition.current if transition else None, ControlMode.GUI)

    def test_profile_yaml_and_generic_adapter_require_confirmed_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "profile.yaml"
            path.write_text(
                """
game: {id: fixture-game, display_name: Fixture Game}
process: {executable: [fixture.exe]}
controls:
  move_forward: {kind: scan_code, code: 17, confirmed: true}
camera: {type: relative_mouse, sensitivity: 100}
capabilities: {realtime_3d: true, gui: false, combat: false, gamepad: false}
capability_level: 3
""".strip(),
                encoding="utf-8",
            )
            environment = GenericEnvironment(load_game_profile(path))
        lifetime = ActionLifetime(UGATime(1), UGATime(1), UGATime(100))
        pressed = environment.adapt_action(CanonicalAction("move", lifetime, move_y=1.0))
        released = environment.adapt_action(CanonicalAction("stop", lifetime))
        self.assertTrue(pressed[0].is_down)  # type: ignore[union-attr]
        self.assertFalse(released[0].is_down)  # type: ignore[union-attr]
        model_config = Path(__file__).resolve().parents[2] / "configs" / "models.yaml"
        models = load_model_registry(model_config)
        self.assertEqual(models.get(ModelRole.MODE_ROUTER).provider, "rules")
        self.assertEqual(
            models.get(ModelRole.PERCEPTION_PRIMARY).model, "qwen3-vl-4b-instruct"
        )
        self.assertFalse(
            models.get(ModelRole.ACTION_VERIFIER, require_enabled=False).enabled
        )

    def test_task_graph_skill_planner_memory_and_recovery(self) -> None:
        root = TaskNode(
            "root",
            "move forward",
            None,
            ("child",),
            TaskStatus.PENDING,
            (),
            "move_forward",
            "progress",
            "no progress",
            1_000,
            RetryPolicy(2),
        )
        child = TaskNode(
            "child",
            "finish",
            "root",
            (),
            TaskStatus.PENDING,
            ("root",),
            None,
            "done",
            "failed",
            1_000,
            RetryPolicy(),
        )
        graph = TaskGraph((root, child))
        self.assertEqual(graph.activate("root").status, TaskStatus.ACTIVE)
        graph.complete("root", success=True)
        self.assertEqual(graph.get("child").status, TaskStatus.READY)

        registry = SkillRegistry()
        registry.register(MoveForwardSkill())
        request_observation = observation()
        planner = QwenVlmPlannerBackend(FakeJsonModel())
        decision = planner.plan(
            PlannerRequest(
                request_observation.user_goal,
                request_observation,
                request_observation.belief_state,
                graph.get("child"),
                registry.available(ControlMode.PLAY_3D),
                (),
                (),
            )
        )
        self.assertEqual(decision.skill, "move_forward")

        clock = ManualClock(10)
        with MemoryStore(":memory:", clock) as memory:
            memory.put(MemoryKind.SEMANTIC, "move_forward", {"key": "W"}, source="test")
            self.assertEqual(memory.query(kind=MemoryKind.SEMANTIC)[0].content, {"key": "W"})
        recovery = ReflectionEngine().reflect(
            FailureReport(
                ReflectionTrigger.NO_PROGRESS,
                FailureKind.NAVIGATION,
                "stuck",
                1,
                0.8,
            )
        )
        self.assertEqual(recovery.strategy, RecoveryStrategy.BACKTRACK)

        cyclic_a = TaskNode(
            "a", "a", None, ("b",), TaskStatus.PENDING, (), None, "yes", "no", 1, RetryPolicy()
        )
        cyclic_b = TaskNode(
            "b", "b", None, ("a",), TaskStatus.PENDING, (), None, "yes", "no", 1, RetryPolicy()
        )
        with self.assertRaisesRegex(ContractViolation, "cycle"):
            TaskGraph((cyclic_a, cyclic_b))

    def test_gui_provider_translates_only_into_leased_proposal(self) -> None:
        obs = observation(1_000, ControlMode.GUI, ("Settings",))
        task = GuiTask("gui-task", "click center", "dialog opens", 2)
        result = UiTarsBackend(FakeGuiModel()).act(task, obs)
        transform = CoordinateTransform(
            Rect(0, 0, 100, 100),
            Rect(0, 0, 100, 100),
            Rect(-100, 50, 100, 250),
            Rect(-110, 20, 110, 260),
            1.0,
        )
        clock = ManualClock(1_000)
        leases = ControlLeaseManager(clock)
        lease = leases.grant(
            ControlOwner.GUI_AGENT,
            ControlMode.GUI,
            1_000,
            confidence=0.9,
            reason="GUI task",
        )
        proposal = GuiControlBridge().proposal(
            result.actions,
            transform,
            lease,
            producer="ui-tars-fixture",
            observation_id=obs.observation_id,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        pointer = proposal.actions[0]
        self.assertEqual(pointer.coordinate_space, CoordinateSpace.PHYSICAL_SCREEN_PIXEL)  # type: ignore[union-attr]
        type_action = GuiAction(
            "type",
            GuiActionKind.TYPE,
            result.actions[0].lifetime,
            text="你好 UGA",
        )
        typed = GuiControlBridge().translate(type_action, transform)
        self.assertIsInstance(typed[0], UnicodeTextAction)
        long_click = GuiAction(
            "long-click",
            GuiActionKind.LONG_CLICK,
            ActionLifetime(UGATime(0), UGATime(0), UGATime(1_000_000_000)),
            x=0.5,
            y=0.5,
        )
        held = GuiControlBridge().translate(long_click, transform)
        self.assertEqual(len(held), 3)
        self.assertIsInstance(held[1], MouseButtonAction)
        self.assertIsInstance(held[2], MouseButtonAction)
        self.assertTrue(held[1].is_down)  # type: ignore[union-attr]
        self.assertFalse(held[2].is_down)  # type: ignore[union-attr]
        self.assertEqual(held[2].lifetime.effective_from.value_ns, 700_000_000)

    def test_first_vertical_slice_records_replays_and_executes(self) -> None:
        clock = ManualClock(100)
        leases = ControlLeaseManager(clock)
        lease = leases.grant(
            ControlOwner.FAST_POLICY,
            ControlMode.PLAY_3D,
            1_000_000_000,
            confidence=1.0,
            reason="vertical slice",
        )
        target = identity()
        backend = DryRunInputBackend()
        guard = FocusGuard(FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True))
        executor = InputExecutor(clock, backend, guard, leases)
        scheduler = ActionScheduler(clock, executor, leases)
        registry = SkillRegistry()
        registry.register(MoveForwardSkill())
        task = TaskNode(
            "task-1",
            "move forward",
            None,
            (),
            TaskStatus.PENDING,
            (),
            "move_forward",
            "progress",
            "no progress",
            1_000_000_000,
            RetryPolicy(),
        )
        obs = observation(200)
        with tempfile.TemporaryDirectory() as temporary:
            metadata = EpisodeMetadata(
                "vertical-slice",
                "fixture-game",
                "1",
                (2, 2),
                "fixture",
                100,
                "move forward",
                EpisodeResult.IN_PROGRESS,
                "baseline-agent",
                None,
                False,
            )
            recorder = EpisodeWriter(temporary, metadata)
            recorder.attach_video(PyAvVideoRecorder(recorder.video_path))
            recorder.record_frame(obs.latest_frame)
            runtime = FirstVerticalSlice(
                FirstAvailableSkillPlanner(),
                registry,
                GenericEnvironment(profile()),
                ActionArbiter(clock, leases),
                scheduler,
                recorder,
            )
            result = runtime.step(obs, TaskGraph((task,)), lease)
            self.assertTrue(result.decision.accepted)
            clock.set(200)
            self.assertEqual(scheduler.tick().executed, 1)
            episode = recorder.finalize(EpisodeResult.SUCCESS, UGATime(300_000_000))
            replay = ReplayEngine(episode)
            self.assertEqual(replay.validation().action_count, 2)
            self.assertTrue(replay.actions_for_observation(obs.observation_id))
            self.assertTrue(any(action["action_layer"] == "canonical" for action in replay.actions))
        self.assertEqual(len(backend.actions), 1)


if __name__ == "__main__":
    unittest.main()
