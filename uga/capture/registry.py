from __future__ import annotations

from dataclasses import dataclass

from uga.capture.base import CaptureBackend, CaptureProbe
from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.windows.window_identity import WindowIdentity


@dataclass(frozen=True, slots=True)
class BackendCandidate:
    backend: CaptureBackend
    probe: CaptureProbe
    preference_index: int


class CaptureBackendRegistry:
    """Selects an honestly-probed backend without leaking platform details."""

    def __init__(self, preference: tuple[str, ...] = ()) -> None:
        self._backends: dict[str, CaptureBackend] = {}
        self._preference = preference

    def register(self, backend: CaptureBackend) -> None:
        if backend.backend_id in self._backends:
            raise ContractViolation(f"duplicate capture backend: {backend.backend_id}")
        self._backends[backend.backend_id] = backend

    def candidates(self, target: WindowIdentity) -> tuple[BackendCandidate, ...]:
        preference = {name: index for index, name in enumerate(self._preference)}
        default_index = len(preference)
        candidates = [
            BackendCandidate(backend, backend.probe(target), preference.get(name, default_index))
            for name, backend in self._backends.items()
        ]
        candidates.sort(
            key=lambda item: (not item.probe.available, item.preference_index, -item.probe.score)
        )
        return tuple(candidates)

    def start_best(
        self, target: WindowIdentity, *, exclude: frozenset[str] = frozenset()
    ) -> CaptureBackend:
        errors: list[str] = []
        for candidate in self.candidates(target):
            if candidate.backend.backend_id in exclude:
                continue
            if not candidate.probe.available:
                errors.append(f"{candidate.backend.backend_id}: {candidate.probe.reason}")
                continue
            try:
                candidate.backend.start(target)
                return candidate.backend
            except Exception as error:
                errors.append(f"{candidate.backend.backend_id}: {error}")
        detail = "; ".join(errors) if errors else "no backends registered"
        raise BackendUnavailableError(f"no capture backend could start ({detail})")
