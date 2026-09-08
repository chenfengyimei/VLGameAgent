from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from uga.agent.belief import BeliefState
from uga.capture.frame import Frame
from uga.control.lease import ControlLease, ControlMode
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.time.clock import UGATime


@dataclass(frozen=True, slots=True)
class LatencyContext:
    capture_age_ns: int
    observation_build_ns: int
    inference_budget_ns: int

    def __post_init__(self) -> None:
        if min(self.capture_age_ns, self.observation_build_ns, self.inference_budget_ns) < 0:
            raise ContractViolation("latency values cannot be negative")


@dataclass(frozen=True, slots=True)
class Observation(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.observation"

    observation_id: str
    created_at: UGATime
    latest_frame: Frame
    frame_history: tuple[Frame, ...]
    current_mode: ControlMode
    user_goal: str
    current_subgoal: str | None
    recent_action_ids: tuple[str, ...]
    recent_events: tuple[str, ...]
    belief_state: BeliefState
    visible_text: tuple[str, ...]
    active_skill: str | None
    control_lease: ControlLease | None
    latency_context: LatencyContext

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.observation_id.strip() or not self.user_goal.strip():
            raise ContractViolation("observation requires id and user goal")
        self.created_at.validate()
        self.latest_frame.validate()
        if self.created_at < self.latest_frame.capture_timestamp:
            raise ContractViolation("observation cannot predate its latest frame")
        if not 1 <= len(self.frame_history) <= 8:
            raise ContractViolation("observation history must contain 1 to 8 frames")
        timestamps = [frame.capture_timestamp for frame in self.frame_history]
        if timestamps != sorted(timestamps) or self.frame_history[-1] != self.latest_frame:
            raise ContractViolation("frame history must be ordered and end with latest frame")
        if self.belief_state.mode != self.current_mode:
            raise ContractViolation("observation and belief mode must match")
        if self.current_subgoal is not None and not self.current_subgoal.strip():
            raise ContractViolation("observation subgoal cannot be blank")
        if any(not event.strip() for event in self.recent_events):
            raise ContractViolation("recent event names cannot be blank")

    def to_envelope(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.SCHEMA_NAME,
            "schema_version": self.SCHEMA_VERSION,
            "data": {
                "observation_id": self.observation_id,
                "created_at_ns": self.created_at.value_ns,
                "latest_frame": self.latest_frame.to_envelope(),
                "frame_history": [frame.to_envelope() for frame in self.frame_history],
                "current_mode": self.current_mode.value,
                "user_goal": self.user_goal,
                "current_subgoal": self.current_subgoal,
                "recent_action_ids": list(self.recent_action_ids),
                "recent_events": list(self.recent_events),
                "belief_state": self.belief_state.to_envelope(),
                "visible_text": list(self.visible_text),
                "active_skill": self.active_skill,
                "control_lease": (
                    None if self.control_lease is None else self.control_lease.to_envelope()
                ),
                "latency_context": {
                    "capture_age_ns": self.latency_context.capture_age_ns,
                    "observation_build_ns": self.latency_context.observation_build_ns,
                    "inference_budget_ns": self.latency_context.inference_budget_ns,
                },
            },
        }
