from __future__ import annotations

import uuid
from dataclasses import dataclass

from uga.agent.planner import PlanDecision, PlannerProvider, PlannerRequest
from uga.agent.skills import SkillExecutorType, SkillLevel, SkillRegistry, SkillSpec
from uga.agent.task_graph import TaskGraph, TaskStatus
from uga.control.arbiter import ActionArbiter, ArbiterDecision
from uga.control.canonical import CanonicalAction
from uga.control.lease import ControlLease, ControlMode
from uga.control.lifetime import ActionLifetime
from uga.control.proposal import ActionProposal
from uga.control.scheduler import ActionScheduler
from uga.core.errors import ContractViolation
from uga.environment.adapter import EnvironmentAdapter
from uga.observation.schema import Observation
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.schema import ActionProvenance
from uga.time.clock import UGATime


@dataclass(frozen=True, slots=True)
class VerticalSliceResult:
    plan: PlanDecision
    proposal: ActionProposal
    decision: ArbiterDecision
    scheduled_actions: int


class FirstVerticalSlice:
    """Goal -> planner -> rule skill -> canonical -> physical -> safe scheduler."""

    def __init__(
        self,
        planner: PlannerProvider,
        skills: SkillRegistry,
        environment: EnvironmentAdapter,
        arbiter: ActionArbiter,
        scheduler: ActionScheduler,
        recorder: EpisodeWriter | None = None,
    ) -> None:
        self._planner = planner
        self._skills = skills
        self._environment = environment
        self._arbiter = arbiter
        self._scheduler = scheduler
        self._recorder = recorder

    def step(
        self, observation: Observation, tasks: TaskGraph, lease: ControlLease
    ) -> VerticalSliceResult:
        ready = next(
            (
                node
                for node in tasks.nodes()
                if node.status in (TaskStatus.READY, TaskStatus.ACTIVE)
            ),
            None,
        )
        if ready is None:
            raise ContractViolation("vertical slice has no ready or active task")
        if ready.status == TaskStatus.READY:
            ready = tasks.activate(ready.node_id)
        available = self._skills.available(observation.current_mode)
        request = PlannerRequest(
            observation.user_goal,
            observation,
            observation.belief_state,
            ready,
            available,
            (),
            (),
        )
        plan = self._planner.plan(request)
        if plan.mode != observation.current_mode:
            raise ContractViolation("planner mode does not match confirmed observation mode")
        skill = self._skills.resolve(plan.skill, mode=plan.mode)
        output = skill.propose(observation, {"target": plan.target})
        if not isinstance(output, CanonicalAction):
            raise ContractViolation("first vertical slice requires a canonical rule-skill output")
        physical = self._environment.adapt_action(output)
        if not physical:
            raise ContractViolation("skill output produced no physical actions")
        proposal = ActionProposal(
            uuid.uuid4().hex,
            f"skill:{skill.spec.skill_id}",
            lease.owner,
            lease.mode,
            lease.lease_id,
            lease.generation,
            output.lifetime,
            physical,
            observation.observation_id,
            1.0,
        )
        decision = self._arbiter.decide(proposal)
        if decision.accepted and self._recorder is not None:
            self._recorder.record_observation(
                observation.observation_id,
                observation.created_at,
                {
                    "latest_frame_id": observation.latest_frame.frame_id,
                    "mode": observation.current_mode.value,
                    "goal": observation.user_goal,
                    "subgoal": plan.subgoal,
                },
            )
            self._recorder.record_task(
                ready.node_id,
                observation.created_at,
                {
                    "instruction": ready.instruction,
                    "status": ready.status.value,
                    "success_condition": ready.success_condition,
                    "failure_condition": ready.failure_condition,
                },
            )
            self._recorder.record_planner(
                uuid.uuid4().hex,
                observation.created_at,
                {
                    "model_version": self._planner.model_version,
                    "subgoal": plan.subgoal,
                    "mode": plan.mode.value,
                    "skill": plan.skill,
                    "target": plan.target,
                    "success_condition": plan.success_condition,
                    "failure_condition": plan.failure_condition,
                },
            )
            self._recorder.record_canonical_action(
                output,
                ActionProvenance(
                    output.action_id,
                    "RULE_SKILL",
                    None,
                    self._planner.model_version,
                    observation.observation_id,
                    skill.spec.skill_id,
                    ready.node_id,
                    plan.mode.value,
                    lease.lease_id,
                    1.0,
                    False,
                    output.lifetime,
                ),
            )
            for action in physical:
                provenance = ActionProvenance(
                    action.action_id,
                    "RULE_SKILL",
                    None,
                    self._planner.model_version,
                    observation.observation_id,
                    skill.spec.skill_id,
                    ready.node_id,
                    plan.mode.value,
                    lease.lease_id,
                    1.0,
                    False,
                    action.lifetime,
                )
                self._recorder.record_action(action, provenance)
        scheduled = self._scheduler.schedule(
            decision, observation.latest_frame.window_identity, lease
        )
        return VerticalSliceResult(plan, proposal, decision, scheduled)


class MoveForwardSkill:
    def __init__(self, duration_ns: int = 200_000_000) -> None:
        if duration_ns <= 0:
            raise ContractViolation("move-forward duration must be positive")
        self._duration_ns = duration_ns
        self._spec = SkillSpec(
            "move_forward",
            "1.0.0",
            "Move forward for one short closed-loop control interval.",
            SkillLevel.ATOMIC,
            (ControlMode.PLAY_3D,),
            ("target window focused",),
            ("target",),
            "visual progress is detected",
            "no progress during the interval",
            duration_ns,
            SkillExecutorType.RULE,
            "record_all",
        )

    @property
    def spec(self) -> SkillSpec:
        return self._spec

    def propose(self, observation: Observation, parameters: dict[str, str]) -> CanonicalAction:
        del parameters
        created = observation.created_at
        lifetime = ActionLifetime(
            created,
            created,
            UGATime(created.value_ns + self._duration_ns),
        )
        return CanonicalAction(uuid.uuid4().hex, lifetime, move_y=1.0)


class FirstAvailableSkillPlanner:
    """Deterministic baseline used before a configured VLM provider is available."""

    @property
    def model_version(self) -> str:
        return "rule-planner-1.0.0"

    def plan(self, request: PlannerRequest) -> PlanDecision:
        if not request.available_skills:
            raise ContractViolation("no skill is available for the current mode")
        skill = request.available_skills[0]
        return PlanDecision(
            request.active_task.instruction,
            request.observation.current_mode,
            skill.skill_id,
            request.active_task.instruction,
            request.active_task.success_condition,
            request.active_task.failure_condition,
        )
