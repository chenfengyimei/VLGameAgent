from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from uga.capture.fallback import GDIFallbackCaptureBackend
from uga.control.executor import InputExecutor
from uga.control.lease import ControlMode, ControlOwner
from uga.control.lease_manager import ControlLeaseManager
from uga.control.lifetime import ActionLifetime
from uga.control.physical import KeyboardAction
from uga.control.windows_input import SendInputBackend
from uga.release.fixture_qualification import FixtureVisualState, analyze_fixture_frame
from uga.safety.focus_guard import AgentEnableState, FocusGuard
from uga.time.clock import PerfCounterClock, UGATime
from uga.windows.backend import Win32WindowBackend, WindowSnapshot
from uga.windows.integrity import Win32IntegrityProvider

_PHYSICAL_TESTS = os.environ.get("UGA_RUN_PHYSICAL_INPUT_TESTS") == "1"


@unittest.skipUnless(os.name == "nt" and _PHYSICAL_TESTS, "physical input test is opt-in")
class PhysicalInputTests(unittest.TestCase):
    def test_send_input_moves_the_owned_fixture(self) -> None:
        executable = Path(sys.executable)
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.is_file():
            executable = pythonw
        process = subprocess.Popen(
            [str(executable), "-m", "apps.example_game"],
            cwd=Path.cwd(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        backend: GDIFallbackCaptureBackend | None = None
        input_backend: SendInputBackend | None = None
        try:
            windows = Win32WindowBackend()
            target = self._wait_for_target(windows, process)
            self.assertTrue(windows.request_foreground(target.identity.hwnd))
            backend = GDIFallbackCaptureBackend(windows)
            backend.start(target.identity)
            before = self._wait_for_rendered_frame(backend)
            self.assertIsNotNone(before.player_center)

            input_backend = SendInputBackend()
            clock = PerfCounterClock()
            now = clock.now()
            leases = ControlLeaseManager(clock)
            lease = leases.grant(
                ControlOwner.FAST_POLICY,
                ControlMode.PLAY_3D,
                5_000_000_000,
                confidence=1.0,
                reason="owned physical fixture test",
            )
            executor = InputExecutor(
                clock,
                input_backend,
                FocusGuard(
                    windows,
                    Win32IntegrityProvider(),
                    leases,
                    AgentEnableState(True),
                ),
                leases,
            )
            lifetime = ActionLifetime(now, now, UGATime(now.value_ns + 5_000_000_000))
            down = executor.execute(
                KeyboardAction("physical-d-down", lifetime, 32, True),
                target.identity,
                lease,
            )
            self.assertTrue(down.executed)
            time.sleep(1.0)
            up = executor.execute(
                KeyboardAction("physical-d-up", lifetime, 32, False),
                target.identity,
                lease,
            )
            self.assertTrue(up.executed)
            time.sleep(0.1)

            after = self._wait_for_rendered_frame(backend)
            assert before.player_center is not None and after.player_center is not None
            self.assertGreater(after.player_center[0], before.player_center[0] + 100)
        finally:
            if input_backend is not None:
                input_backend.release_all()
            if backend is not None:
                backend.stop()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5.0)

    @staticmethod
    def _wait_for_target(
        windows: Win32WindowBackend,
        process: subprocess.Popen[bytes],
    ) -> WindowSnapshot:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            target = next(
                (
                    item
                    for item in windows.discover()
                    if item.title == "UGA Fixture World" and item.identity.pid == process.pid
                ),
                None,
            )
            if target is not None:
                return target
            if process.poll() is not None:
                break
            time.sleep(0.05)
        raise AssertionError("owned fixture window did not become ready")

    @staticmethod
    def _wait_for_rendered_frame(backend: GDIFallbackCaptureBackend) -> FixtureVisualState:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            state = analyze_fixture_frame(backend.capture())
            if state.player_center is not None:
                return state
            time.sleep(0.05)
        raise AssertionError("owned fixture did not render its player")


if __name__ == "__main__":
    unittest.main()
