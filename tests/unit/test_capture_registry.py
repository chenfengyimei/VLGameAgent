from __future__ import annotations

import unittest

from tests.helpers import frame, identity
from uga.capture.base import (
    CaptureBackend,
    CaptureCapability,
    CaptureProbe,
    GraphicsAPI,
    WindowMode,
)
from uga.capture.frame import Frame, PixelFormat
from uga.capture.registry import CaptureBackendRegistry
from uga.core.errors import BackendUnavailableError
from uga.windows.window_identity import WindowIdentity

CAPABILITY = CaptureCapability(
    frozenset({WindowMode.WINDOWED}),
    frozenset({GraphicsAPI.UNKNOWN}),
    frozenset({PixelFormat.BGRA8}),
    False,
    False,
    True,
    False,
)


class FakeBackend(CaptureBackend):
    def __init__(
        self,
        backend_id: str,
        available: bool,
        score: int,
        start_fails: bool = False,
        stop_fails: bool = False,
    ) -> None:
        self.backend_id = backend_id
        self.available = available
        self.score = score
        self.start_fails = start_fails
        self.stop_fails = stop_fails
        self.stop_calls = 0
        super().__init__()

    def probe(self, target: WindowIdentity) -> CaptureProbe:
        return CaptureProbe(self.available, self.score, "test probe", CAPABILITY)

    def _start(self, target: WindowIdentity) -> None:
        if self.start_fails:
            raise RuntimeError("start failed")

    def _capture(self) -> Frame:
        return frame(1, backend=self.backend_id)

    def _stop(self) -> None:
        self.stop_calls += 1
        if self.stop_fails:
            raise RuntimeError("stop failed")


class CaptureRegistryTests(unittest.TestCase):
    def test_preference_wins_over_probe_score(self) -> None:
        registry = CaptureBackendRegistry(("preferred", "faster"))
        preferred = FakeBackend("preferred", True, 10)
        registry.register(FakeBackend("faster", True, 100))
        registry.register(preferred)
        self.assertIs(registry.start_best(identity()), preferred)

    def test_start_failure_falls_back(self) -> None:
        registry = CaptureBackendRegistry(("first", "second"))
        first = FakeBackend("first", True, 100, start_fails=True)
        registry.register(first)
        second = FakeBackend("second", True, 50)
        registry.register(second)
        self.assertIs(registry.start_best(identity()), second)
        self.assertEqual(first.stop_calls, 1)

    def test_start_failure_preserves_error_when_partial_cleanup_also_fails(self) -> None:
        backend = FakeBackend("broken", True, 100, start_fails=True, stop_fails=True)

        with self.assertRaisesRegex(RuntimeError, "start failed") as raised:
            backend.start(identity())

        self.assertEqual(backend.stop_calls, 1)
        self.assertIn("partial-start cleanup failed", "\n".join(raised.exception.__notes__))

    def test_no_available_backend_reports_failure(self) -> None:
        registry = CaptureBackendRegistry()
        registry.register(FakeBackend("none", False, 0))
        with self.assertRaises(BackendUnavailableError):
            registry.start_best(identity())


if __name__ == "__main__":
    unittest.main()
