from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from uga.control.lifetime import ActionLifetime
from uga.control.physical import PhysicalAction
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.time.clock import UGATime


class EpisodeResult(StrEnum):
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILURE = "failure"
    ABORTED = "aborted"


class TimelineKind(StrEnum):
    FRAME = "frame"
    RAW_INPUT = "raw_input"
    ACTION = "action"
    OBSERVATION = "observation"
    EVENT = "event"
    ANNOTATION = "annotation"
    TASK = "task"
    PLANNER = "planner"


class RecordedActionLayer(StrEnum):
    SEMANTIC = "semantic"
    CANONICAL = "canonical"
    PHYSICAL = "physical"


@dataclass(frozen=True, slots=True)
class EpisodeMetadata(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.episode_metadata"

    episode_id: str
    game_id: str
    game_version: str
    window_size: tuple[int, int]
    capture_backend: str
    start_monotonic_ns: int
    task: str
    result: EpisodeResult
    agent_version: str
    policy_version: str | None
    human_controlled: bool
    end_monotonic_ns: int | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        required = (
            self.episode_id,
            self.game_id,
            self.game_version,
            self.capture_backend,
            self.task,
            self.agent_version,
        )
        if any(not item.strip() for item in required):
            raise ContractViolation("episode metadata identifiers cannot be blank")
        width, height = self.window_size
        if width <= 0 or height <= 0:
            raise ContractViolation("episode window size must be positive")
        UGATime(self.start_monotonic_ns)
        if self.end_monotonic_ns is not None:
            UGATime(self.end_monotonic_ns)
            if self.end_monotonic_ns < self.start_monotonic_ns:
                raise ContractViolation("episode end cannot precede start")


@dataclass(frozen=True, slots=True)
class ActionProvenance(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.action_provenance"

    action_id: str
    action_source: str
    policy_version: str | None
    model_checkpoint: str | None
    observation_id: str | None
    skill_id: str | None
    task_node_id: str | None
    mode: str
    lease_id: str
    confidence: float
    human_override: bool
    lifetime: ActionLifetime

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.action_id.strip() or not self.action_source.strip():
            raise ContractViolation("action provenance requires action id and source")
        if not self.mode.strip() or not self.lease_id.strip():
            raise ContractViolation("action provenance requires mode and lease id")
        if not 0.0 <= self.confidence <= 1.0:
            raise ContractViolation("action provenance confidence must be in [0, 1]")
        self.lifetime.validate()


@dataclass(frozen=True, slots=True)
class InputStateRecord(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.input_state_record"

    timestamp: UGATime
    action: PhysicalAction
    keyboard_down: tuple[str, ...]
    mouse_buttons_down: tuple[str, ...]

    def __post_init__(self) -> None:
        self.action.validate()
        if any(not key.strip() for key in self.keyboard_down):
            raise ContractViolation("keyboard state names cannot be blank")


@dataclass(frozen=True, slots=True)
class TimelineRecord:
    sequence: int
    timestamp_ns: int
    elapsed_ns: int
    kind: TimelineKind
    reference_id: str
    payload_json: str

    def __post_init__(self) -> None:
        if self.sequence < 1 or self.timestamp_ns < 0 or self.elapsed_ns < 0:
            raise ContractViolation("timeline sequence and timestamps must be non-negative")
        if not self.reference_id.strip():
            raise ContractViolation("timeline reference id cannot be blank")
