from __future__ import annotations

import unittest
from pathlib import Path

from uga.capture.frame import BufferHandle, BufferKind, Frame, PixelFormat
from uga.core.errors import ContractViolation
from uga.release.fixture_corpus import run_fixture_corpus
from uga.release.fixture_qualification import (
    _emergency_hotkey_passed,
    _select_owned_fixture_target,
    _watchdog_timeout_passed,
    analyze_fixture_frame,
)
from uga.safety.focus_guard import AgentEnableState
from uga.safety.shutdown import SafetyTrip, ShutdownCause
from uga.time.clock import UGATime
from uga.windows.backend import WindowSnapshot
from uga.windows.coordinates import Rect
from uga.windows.window_identity import WindowIdentity


class FixtureQualificationTests(unittest.TestCase):
    def test_fixture_target_requires_owned_process_identity(self) -> None:
        selected_identity = WindowIdentity(1, 10, "a" * 64, 1, 1)
        expected = WindowIdentity(1, 10, "a" * 64, 1, 2)
        decoy = WindowIdentity(2, 11, "b" * 64, 2, 1)

        class Windows:
            @staticmethod
            def discover() -> tuple[WindowSnapshot, ...]:
                rect = Rect(0, 0, 100, 100)
                return (
                    WindowSnapshot(decoy, "UGA Fixture World", rect, rect, 96, True, True),
                    WindowSnapshot(
                        selected_identity, "UGA Fixture World", rect, rect, 96, True, False
                    ),
                )

        selected = _select_owned_fixture_target(
            Windows(),  # type: ignore[arg-type]
            title_pattern="^UGA Fixture World$",
            expected_pid=10,
            expected_identity=expected,
        )
        self.assertEqual(selected.identity, selected_identity)
        with self.assertRaisesRegex(ContractViolation, "owned window"):
            _select_owned_fixture_target(
                Windows(),  # type: ignore[arg-type]
                title_pattern="^UGA Fixture World$",
                expected_pid=12,
                expected_identity=None,
            )

    def test_emergency_exercise_requires_hotkey_cause_and_executed_inputs(self) -> None:
        watchdog_trip = SafetyTrip(ShutdownCause.WATCHDOG_TIMEOUT, UGATime(1), 0)
        emergency_trip = SafetyTrip(ShutdownCause.EMERGENCY_HOTKEY, UGATime(1), 0)

        self.assertFalse(_emergency_hotkey_passed(["executed"] * 3, watchdog_trip))
        self.assertFalse(
            _emergency_hotkey_passed(
                ["executed", "agent_disabled", "agent_disabled"], emergency_trip
            )
        )
        self.assertTrue(_emergency_hotkey_passed(["executed"] * 3, emergency_trip))

    def test_watchdog_exercise_requires_timeout_cause_and_fail_closed_state(self) -> None:
        emergency_trip = SafetyTrip(ShutdownCause.EMERGENCY_HOTKEY, UGATime(1), 0)
        watchdog_trip = SafetyTrip(ShutdownCause.WATCHDOG_TIMEOUT, UGATime(1), 0)
        dirty_watchdog_trip = SafetyTrip(
            ShutdownCause.WATCHDOG_TIMEOUT, UGATime(1), 0, ("queue flush failed: boom",)
        )
        disabled = AgentEnableState(True)
        disabled.set(False)
        still_enabled = AgentEnableState(True)

        self.assertFalse(_watchdog_timeout_passed(None, disabled, False))
        self.assertFalse(_watchdog_timeout_passed(emergency_trip, disabled, False))
        self.assertFalse(_watchdog_timeout_passed(dirty_watchdog_trip, disabled, False))
        self.assertFalse(_watchdog_timeout_passed(watchdog_trip, still_enabled, False))
        self.assertFalse(_watchdog_timeout_passed(watchdog_trip, disabled, True))
        self.assertTrue(_watchdog_timeout_passed(watchdog_trip, disabled, False))

    def test_release_corpus_requires_explicit_input_and_five_train_hours(self) -> None:
        with self.assertRaisesRegex(ContractViolation, "allow-physical-input"):
            run_fixture_corpus(
                project_root=Path("."),
                output_root=Path("unused"),
                train_duration_seconds=6000,
                test_duration_seconds=600,
                target_fps=3,
                backend="gdi_fallback",
                allow_physical_input=False,
            )
        with self.assertRaisesRegex(ContractViolation, "five train hours"):
            run_fixture_corpus(
                project_root=Path("."),
                output_root=Path("unused"),
                train_duration_seconds=4.5,
                test_duration_seconds=4.5,
                target_fps=3,
                backend="gdi_fallback",
                allow_physical_input=True,
            )

    def test_fixture_color_analysis_uses_captured_pixels(self) -> None:
        width, height = 16, 8
        pixels = bytearray(width * height * 4)
        colors = (
            ((248, 189, 56, 255), 24),
            ((94, 197, 34, 255), 24),
            ((21, 204, 250, 255), 24),
        )
        index = 0
        for color, count in colors:
            for _ in range(count):
                pixels[index : index + 4] = bytes(color)
                index += 4
        identity = WindowIdentity(1, 1, "a" * 64, 1, 1)
        frame = Frame(
            "fixture-frame",
            UGATime(1),
            None,
            identity,
            width,
            height,
            width * 4,
            PixelFormat.BGRA8,
            Rect(0, 0, width, height),
            Rect(0, 0, width, height),
            "test",
            BufferHandle("pixels", BufferKind.CPU_BYTES, len(pixels), pixels),
        )

        state = analyze_fixture_frame(frame)

        self.assertEqual(state.player_pixels, 24)
        self.assertEqual(state.target_pixels, 24)
        self.assertEqual(state.success_pixels, 24)
        self.assertTrue(state.success_visible)

    def test_fixture_color_analysis_rejects_non_cpu_frame(self) -> None:
        identity = WindowIdentity(1, 1, "a" * 64, 1, 1)
        frame = Frame(
            "fixture-frame",
            UGATime(1),
            None,
            identity,
            1,
            1,
            4,
            PixelFormat.BGRA8,
            Rect(0, 0, 1, 1),
            Rect(0, 0, 1, 1),
            "test",
            BufferHandle("native", BufferKind.NATIVE, 0, object()),
        )
        with self.assertRaisesRegex(ContractViolation, "CPU-addressable"):
            analyze_fixture_frame(frame)


if __name__ == "__main__":
    unittest.main()
