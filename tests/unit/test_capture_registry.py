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
        self, backend_id: str, available: bool, score: int, start_fails: bool = False
    ) -> None:
        self.backend_id = backend_id
        self.available = available
        self.score = score
        self.start_fails = start_fails
        super().__init__()

    def probe(self, target: WindowIdentity) -> CaptureProbe:
        return CaptureProbe(self.available, self.score, "test probe", CAPABILITY)

    def _start(self, target: WindowIdentity) -> None:
        if self.start_fails:
            raise RuntimeError("start failed")

    def _capture(self) -> Frame:
        return frame(1, backend=self.backend_id)

    def _stop(self) -> None:
        pass


class CaptureRegistryTests(unittest.TestCase):
    def test_preference_wins_over_probe_score(self) -> None:
        registry = CaptureBackendRegistry(("preferred", "faster"))
        preferred = FakeBackend("preferred", True, 10)
        registry.register(FakeBackend("faster", True, 100))
        registry.register(preferred)
        self.assertIs(registry.start_best(identity()), preferred)

    def test_start_failure_falls_back(self) -> None:
        registry = CaptureBackendRegistry(("first", "second"))
        registry.register(FakeBackend("first", True, 100, start_fails=True))
        second = FakeBackend("second", True, 50)
        registry.register(second)
        self.assertIs(registry.start_best(identity()), second)

    def test_no_available_backend_reports_failure(self) -> None:
        registry = CaptureBackendRegistry()
        registry.register(FakeBackend("none", False, 0))
        with self.assertRaises(BackendUnavailableError):
            registry.start_best(identity())


if __name__ == "__main__":
    unittest.main()
