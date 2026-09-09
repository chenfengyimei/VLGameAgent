from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from uga.agent.belief import BeliefState
from uga.agent.skills import SkillSpec
from uga.agent.task_graph import TaskNode
from uga.control.lease import ControlMode
from uga.core.artifact_limits import parse_json_text
from uga.core.errors import ContractViolation
from uga.observation.schema import Observation


@dataclass(frozen=True, slots=True)
class PlannerRequest:
    user_goal: str
    observation: Observation
    belief: BeliefState
    active_task: TaskNode
    available_skills: tuple[SkillSpec, ...]
    recent_failures: tuple[str, ...]
    memory_context: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanDecision:
    subgoal: str
    mode: ControlMode
    skill: str
    target: str
    success_condition: str
    failure_condition: str

    def __post_init__(self) -> None:
        fields = (
            self.subgoal,
            self.skill,
            self.target,
            self.success_condition,
            self.failure_condition,
        )
        if any(not field.strip() for field in fields):
            raise ContractViolation("planner decision fields cannot be blank")


@runtime_checkable
class PlannerProvider(Protocol):
    @property
    def model_version(self) -> str: ...

    def plan(self, request: PlannerRequest) -> PlanDecision: ...


@runtime_checkable
class MultimodalJsonModel(Protocol):
    @property
    def model_version(self) -> str: ...

    def generate_json(self, *, instruction: str, observation: Observation) -> str: ...


class QwenVlmPlannerBackend:
    """Schema-constrained Qwen planner adapter; model loading stays outside business logic."""

    def __init__(self, model: MultimodalJsonModel) -> None:
        self._model = model

    @property
    def model_version(self) -> str:
        return self._model.model_version

    def plan(self, request: PlannerRequest) -> PlanDecision:
        skills = ", ".join(skill.skill_id for skill in request.available_skills)
        instruction = (
            "Return only a JSON object with keys subgoal, mode, skill, target, "
            "success_condition, failure_condition. Never output physical input. "
            f"Goal: {request.user_goal}. Active task: {request.active_task.instruction}. "
            f"Available skills: {skills}."
        )
        raw = self._model.generate_json(instruction=instruction, observation=request.observation)
        try:
            payload = parse_json_text(raw)
            if not isinstance(payload, dict):
                raise TypeError("planner output is not an object")
            decision = PlanDecision(
                subgoal=str(payload["subgoal"]),
                mode=ControlMode(str(payload["mode"]).casefold()),
                skill=str(payload["skill"]),
                target=str(payload["target"]),
                success_condition=str(payload["success_condition"]),
                failure_condition=str(payload["failure_condition"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractViolation("planner returned invalid schema") from exc
        if decision.skill not in {skill.skill_id for skill in request.available_skills}:
            raise ContractViolation("planner selected an unavailable skill")
        return decision
