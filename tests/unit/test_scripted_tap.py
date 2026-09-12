from __future__ import annotations

import unittest
from unittest import mock

from tests.helpers import identity
from tests.integration.test_baseline_agent import FakeIntegrity, FakeWindows
from uga.control.arbiter import ActionArbiter
from uga.control.executor import InputExecutor
from uga.control.input_backend import DryRunInputBackend
from uga.control.lease import ControlMode, ControlOwner
from uga.control.lease_manager import ControlLeaseManager
from uga.control.physical import AbsolutePointerAction, MouseButtonAction
from uga.control.scheduler import ActionScheduler
from uga.core.errors import ContractViolation
from uga.environment.generic import GenericEnvironment
from uga.environment.profile import (
    BindingKind,
    ControlBinding,
    EnvironmentCapabilityLevel,
    GameCapabilities,
    GameProfile,
)
from uga.policy.action_chunk import ActionButton, ActionChunk
from uga.policy.chunk_controller import ActionChunkController, expand_action_chunk
from uga.policy.fast_policy import PolicyContext
from uga.policy.scripted_tap import ScriptedTapPolicy
from uga.safety.focus_guard import AgentEnableState, FocusGuard
from uga.time.clock import ManualClock, UGATime


def absolute_pointer_profile(confirmed: bool = True) -> GameProfile:
    return GameProfile(
        "mumu-xianyu",
        "MuMu Fixture",
        ("MuMuNxDevice.exe",),
        "auto",
        (ControlBinding("interact", BindingKind.MOUSE_BUTTON, "left", confirmed),),
        "absolute_pointer",
        1.0,
        GameCapabilities(True, True, False, False),
        EnvironmentCapabilityLevel.USER_CONFIRMED_PROFILE,
    )


def _context() -> PolicyContext:
    return PolicyContext("obs-1", UGATime(100), (), None)


def _chunk(buttons: tuple[int, ...], tick_rate_hz: float) -> ActionChunk:
    ticks = len(buttons)
    return ActionChunk(
        "chunk-1",
        "obs-1",
        UGATime(100),
        UGATime(100),
        UGATime(100 + 1_000_000_000),
        tick_rate_hz,
        (0.0,) * ticks,
        (0.0,) * ticks,
        (0.0,) * ticks,
        (0.0,) * ticks,
        buttons,
        1.0,
        "scripted-tap-v1",
        3200.0,
        890.0,
    )


class ScriptedTapPolicyTests(unittest.TestCase):
    def test_idle_chunk_before_due_carries_hold_coordinates(self) -> None:
        policy = ScriptedTapPolicy([(2.0, 3200, 890)])

        output = policy.infer(_context())

        self.assertEqual(output.chunk.buttons, (0,) * output.chunk.horizon)
        self.assertEqual(output.chunk.pointer_x, 3200.0)
        self.assertEqual(output.chunk.pointer_y, 890.0)

    def test_due_tap_fires_with_pointer_coordinates(self) -> None:
        clock = [0.0]
        with mock.patch("uga.policy.scripted_tap.time.monotonic", lambda: clock[0]):
            policy = ScriptedTapPolicy([(1.0, 3200, 890)])
            clock[0] = 1.2

            output = policy.infer(_context())

        self.assertEqual(output.chunk.buttons, (int(ActionButton.INTERACT),))
        self.assertEqual(output.chunk.pointer_x, 3200.0)
        self.assertEqual(output.chunk.pointer_y, 890.0)

    def test_late_tap_is_not_silently_dropped(self) -> None:
        clock = [0.0]
        with mock.patch("uga.policy.scripted_tap.time.monotonic", lambda: clock[0]):
            policy = ScriptedTapPolicy([(1.0, 3200, 890)])
            clock[0] = 9.0
            output = policy.infer(_context())

        self.assertEqual(output.chunk.buttons, (int(ActionButton.INTERACT),))

    def test_exhausted_timeline_keeps_last_tap_position(self) -> None:
        clock = [0.0]
        with mock.patch("uga.policy.scripted_tap.time.monotonic", lambda: clock[0]):
            policy = ScriptedTapPolicy([(1.0, 3200, 890)])
            clock[0] = 5.0
            policy.infer(_context())

            output = policy.infer(_context())

        self.assertEqual(output.chunk.buttons, (0,) * output.chunk.horizon)
        self.assertEqual(output.chunk.pointer_x, 3200.0)
        self.assertEqual(output.chunk.pointer_y, 890.0)

    def test_repeat_interval_rearms_the_timeline(self) -> None:
        clock = [0.0]
        with mock.patch("uga.policy.scripted_tap.time.monotonic", lambda: clock[0]):
            policy = ScriptedTapPolicy([(1.0, 3200, 890)], repeat_interval_s=5.0)
            clock[0] = 1.2
            first = policy.infer(_context())
            self.assertEqual(first.chunk.buttons, (int(ActionButton.INTERACT),))

            clock[0] = 3.0
            waiting = policy.infer(_context())
            self.assertEqual(waiting.chunk.buttons, (0,) * waiting.chunk.horizon)

            clock[0] = 6.0  # cycle 2 started at t=5; tap due at 5+1=6
            second = policy.infer(_context())
            self.assertEqual(second.chunk.buttons, (int(ActionButton.INTERACT),))

    def test_repeat_skips_fully_missed_cycles_without_bursting(self) -> None:
        clock = [0.0]
        with mock.patch("uga.policy.scripted_tap.time.monotonic", lambda: clock[0]):
            policy = ScriptedTapPolicy([(1.0, 3200, 890)], repeat_interval_s=5.0)
            clock[0] = 12.0  # cycles at t=5 and t=10 passed unseen

            fired = policy.infer(_context())
            self.assertEqual(fired.chunk.buttons, (int(ActionButton.INTERACT),))

            settled = policy.infer(_context())
            self.assertEqual(settled.chunk.buttons, (0,) * settled.chunk.horizon)

    def test_repeat_interval_must_exceed_every_tap_delay(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceed every tap delay"):
            ScriptedTapPolicy([(5.0, 3200, 890)], repeat_interval_s=5.0)

    def test_repeat_interval_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            ScriptedTapPolicy([(1.0, 3200, 890)], repeat_interval_s=-1.0)


class AbsolutePointerEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = GenericEnvironment(absolute_pointer_profile())

    def test_tap_tick_emits_move_down_up_in_order(self) -> None:
        canonical = expand_action_chunk(
            _chunk((int(ActionButton.INTERACT),), 1.0)
        )[0]

        physical = self.environment.adapt_action(canonical)

        self.assertEqual(len(physical), 3)
        self.assertIsInstance(physical[0], AbsolutePointerAction)
        self.assertEqual((physical[0].x, physical[0].y), (3200, 890))
        self.assertIsInstance(physical[1], MouseButtonAction)
        self.assertTrue(physical[1].is_down)
        self.assertIsInstance(physical[2], MouseButtonAction)
        self.assertFalse(physical[2].is_down)

    def test_idle_tick_with_hold_coordinates_is_executable(self) -> None:
        policy = ScriptedTapPolicy([(2.0, 3200, 890)])
        idle = policy.infer(_context()).chunk

        for canonical in expand_action_chunk(idle):
            physical = self.environment.adapt_action(canonical)
            self.assertEqual(len(physical), 1)
            self.assertIsInstance(physical[0], AbsolutePointerAction)

    def test_pointer_coordinates_require_absolute_pointer_camera(self) -> None:
        profile = absolute_pointer_profile()
        relative = GameProfile(
            profile.game_id,
            profile.display_name,
            profile.executables,
            profile.preferred_capture,
            profile.controls,
            "relative_mouse",
            100.0,
            profile.capabilities,
            profile.capability_level,
        )
        environment = GenericEnvironment(relative)
        policy = ScriptedTapPolicy([(2.0, 3200, 890)])
        canonical = expand_action_chunk(policy.infer(_context()).chunk)[0]

        with self.assertRaisesRegex(ContractViolation, "absolute-pointer camera"):
            environment.adapt_action(canonical)

    def test_unconfirmed_mouse_binding_is_rejected(self) -> None:
        profile = absolute_pointer_profile()
        unconfirmed = GameProfile(
            profile.game_id,
            profile.display_name,
            profile.executables,
            profile.preferred_capture,
            (ControlBinding("interact", BindingKind.MOUSE_BUTTON, "left", False),),
            profile.camera_type,
            profile.camera_sensitivity,
            profile.capabilities,
            EnvironmentCapabilityLevel.GENERIC,
        )
        environment = GenericEnvironment(unconfirmed)
        canonical = expand_action_chunk(_chunk((int(ActionButton.INTERACT),), 1.0))[0]

        with self.assertRaisesRegex(ContractViolation, "user-confirmed"):
            environment.adapt_action(canonical)


class IdleChunkSubmissionTests(unittest.TestCase):
    def test_idle_chunk_submits_at_least_one_physical_action(self) -> None:
        clock = ManualClock(100)
        target = identity()
        leases = ControlLeaseManager(clock)
        executor = InputExecutor(
            clock,
            DryRunInputBackend(),
            FocusGuard(FakeWindows(target), FakeIntegrity(), leases, AgentEnableState(True)),
            leases,
        )
        scheduler = ActionScheduler(clock, executor, leases)
        environment = GenericEnvironment(absolute_pointer_profile())
        controller = ActionChunkController(environment, ActionArbiter(clock, leases), scheduler)
        policy = ScriptedTapPolicy([(2.0, 3200, 890)])
        lease = leases.grant(
            ControlOwner.FAST_POLICY,
            ControlMode.PLAY_3D,
            10_000_000_000,
            confidence=1.0,
            reason="test",
        )

        submission = controller.submit(policy.infer(_context()).chunk, target, lease)

        self.assertGreaterEqual(submission.scheduled_physical_actions, 1)


if __name__ == "__main__":
    unittest.main()
