from __future__ import annotations

import argparse
import contextlib
import io
import re
import unittest
from unittest.mock import patch

from apps.agent.__main__ import _run_safely
from apps.agent.run import (
    _best_effort_cleanup,
    _client_fraction,
    _current_target_client_rect,
    _find_target,
)
from tests.helpers import identity
from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.environment.profile import (
    EnvironmentCapabilityLevel,
    GameCapabilities,
    GameProfile,
)
from uga.safety.environment_policy import EnvironmentClass, EnvironmentSafetyManifest
from uga.windows.backend import WindowSnapshot
from uga.windows.coordinates import Rect


def _profile() -> GameProfile:
    return GameProfile(
        "probe",
        "Probe",
        ("trusted.exe",),
        "auto",
        (),
        "relative_mouse",
        1.0,
        GameCapabilities(True, True, False, False),
        EnvironmentSafetyManifest(EnvironmentClass.DEVELOPER_OWNED, True, False, False),
        EnvironmentCapabilityLevel.GENERIC,
        r"^Trusted Window$",
    )


def _snapshot(generation: int = 1, *, visible: bool = True) -> WindowSnapshot:
    return WindowSnapshot(
        identity(generation),
        "Trusted Window",
        Rect(0, 0, 100, 100),
        Rect(1, 2, 99, 98),
        96,
        visible,
        True,
    )


class FakeWindows:
    def __init__(
        self,
        *,
        trusted: tuple[WindowSnapshot, ...] = (),
        unfiltered: tuple[WindowSnapshot, ...] = (),
        current: WindowSnapshot | None = None,
    ) -> None:
        self.trusted = trusted
        self.unfiltered = unfiltered
        self.current = current or (trusted[0] if trusted else _snapshot())
        self.discovery_requests: list[str | None] = []

    def discover(self, *, executable_name: str | None = None) -> tuple[WindowSnapshot, ...]:
        self.discovery_requests.append(executable_name)
        return self.trusted if executable_name == "trusted.exe" else self.unfiltered

    def snapshot(self, hwnd: int) -> WindowSnapshot:
        if hwnd != self.current.identity.hwnd:
            raise AssertionError("unexpected hwnd")
        return self.current

    def foreground_hwnd(self) -> int | None:
        return self.current.identity.hwnd


class LiveAgentCompositionTests(unittest.TestCase):
    def test_cli_renders_contract_rejection_without_traceback(self) -> None:
        error = ContractViolation("environment safety policy rejected runtime")
        stderr = io.StringIO()
        with (
            patch("apps.agent.__main__._run", side_effect=error),
            contextlib.redirect_stderr(stderr),
        ):
            result = _run_safely(argparse.Namespace())

        self.assertEqual(result, 2)
        self.assertEqual(
            stderr.getvalue(),
            "uga-agent: environment safety policy rejected runtime\n",
        )
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_target_discovery_requires_a_profile_executable_match(self) -> None:
        spoof = _snapshot()
        windows = FakeWindows(unfiltered=(spoof,))

        with self.assertRaisesRegex(SystemExit, "trusted executable/window pair"):
            _find_target(windows, _profile())

        self.assertEqual(windows.discovery_requests, ["trusted.exe"])

    def test_target_discovery_returns_the_trusted_match(self) -> None:
        trusted = _snapshot()
        windows = FakeWindows(trusted=(trusted,))

        self.assertEqual(_find_target(windows, _profile()), trusted)

    def test_current_rect_rejects_recycled_window_identity(self) -> None:
        original = _snapshot(1)
        windows = FakeWindows(current=_snapshot(2))

        with self.assertRaisesRegex(BackendUnavailableError, "identity changed"):
            _current_target_client_rect(windows, original, re.compile(r"^Trusted Window$"))

    def test_current_rect_rejects_hidden_target(self) -> None:
        original = _snapshot()
        windows = FakeWindows(current=_snapshot(visible=False))

        with self.assertRaisesRegex(BackendUnavailableError, "no longer matches"):
            _current_target_client_rect(windows, original, re.compile(r"^Trusted Window$"))

    def test_client_fraction_rejects_nan(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            _client_fraction("nan")

    def test_setup_cleanup_continues_after_an_operation_fails(self) -> None:
        called: list[str] = []

        def broken() -> None:
            called.append("broken")
            raise RuntimeError("cleanup failure")

        _best_effort_cleanup(
            broken,
            lambda: called.append("second"),
            lambda: called.append("third"),
        )

        self.assertEqual(called, ["broken", "second", "third"])


if __name__ == "__main__":
    unittest.main()
