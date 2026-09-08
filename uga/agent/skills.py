from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol, TypeAlias, runtime_checkable

from uga.control.canonical import CanonicalAction
from uga.control.lease import ControlMode
from uga.control.semantic import SemanticAction
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.observation.schema import Observation


class SkillLevel(StrEnum):
    ATOMIC = "atomic"
    MOTOR = "motor"
    COMPOSITE = "composite"


class SkillExecutorType(StrEnum):
    RULE = "rule"
    POLICY = "policy"
    GUI = "gui"
    COMPOSITE = "composite"


SkillOutput: TypeAlias = SemanticAction | CanonicalAction


@dataclass(frozen=True, slots=True)
class SkillSpec(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.skill"

    skill_id: str
    version: str
    description: str
    level: SkillLevel
    supported_modes: tuple[ControlMode, ...]
    preconditions: tuple[str, ...]
    parameters: tuple[str, ...]
    success_condition: str
    failure_condition: str
    timeout_ns: int
    executor_type: SkillExecutorType
    telemetry_policy: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        required = (
            self.skill_id,
            self.version,
            self.description,
            self.success_condition,
            self.failure_condition,
            self.telemetry_policy,
        )
        if any(not item.strip() for item in required) or not self.supported_modes:
            raise ContractViolation("skill contract has missing required fields")
        if self.timeout_ns <= 0:
            raise ContractViolation("skill timeout must be positive")
        if len(self.parameters) != len(set(self.parameters)):
            raise ContractViolation("skill parameters must be unique")


@runtime_checkable
class Skill(Protocol):
    @property
    def spec(self) -> SkillSpec: ...

    def propose(self, observation: Observation, parameters: dict[str, str]) -> SkillOutput: ...


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[tuple[str, str], Skill] = {}

    def register(self, skill: Skill) -> None:
        skill.spec.validate()
        key = (skill.spec.skill_id, skill.spec.version)
        if key in self._skills:
            raise ContractViolation(f"skill already registered: {key}")
        self._skills[key] = skill

    def resolve(self, skill_id: str, *, mode: ControlMode, version: str | None = None) -> Skill:
        candidates = [
            skill
            for (candidate_id, candidate_version), skill in self._skills.items()
            if candidate_id == skill_id and (version is None or candidate_version == version)
        ]
        if not candidates:
            raise KeyError(skill_id)
        candidates.sort(key=lambda skill: skill.spec.version, reverse=True)
        selected = candidates[0]
        if mode not in selected.spec.supported_modes:
            raise ContractViolation(f"skill {skill_id!r} does not support mode {mode}")
        return selected

    def available(self, mode: ControlMode) -> tuple[SkillSpec, ...]:
        return tuple(
            skill.spec for skill in self._skills.values() if mode in skill.spec.supported_modes
        )
