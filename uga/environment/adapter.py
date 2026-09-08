from __future__ import annotations

from typing import Protocol, runtime_checkable

from uga.control.canonical import CanonicalAction
from uga.control.physical import PhysicalAction
from uga.environment.profile import GameProfile
from uga.observation.schema import Observation


@runtime_checkable
class EnvironmentAdapter(Protocol):
    @property
    def profile(self) -> GameProfile: ...

    def adapt_action(self, action: CanonicalAction) -> tuple[PhysicalAction, ...]: ...

    def enrich_observation(self, observation: Observation) -> Observation: ...
