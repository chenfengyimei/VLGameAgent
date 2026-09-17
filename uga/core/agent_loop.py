from __future__ import annotations

import asyncio
import contextlib
import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Protocol, runtime_checkable

from uga.agent.closed_loop import (
    ActionValidator,
    ClosedLoopSupervisor,
    DecisionDisposition,
    KeyResolver,
    SupervisedDecision,
    TerminalStatus,
)
from uga.agent.mode_router import ModeClassifier, ModeRouter, ModeTransition
from uga.agent.recovery_budget import RecoveryBudget
from uga.agent.session_state import quest_level_target
from uga.capture.frame import Frame
from uga.capture.ring_buffer import FrameRingBuffer, LatestFrameSlot, SequencedFrame
from uga.control.arbiter import ArbiterDecision
from uga.control.lease import ControlMode, ControlOwner
from uga.control.lease_manager import ControlLeaseManager
from uga.control.physical import AbsolutePointerAction, PhysicalAction
from uga.control.scheduler import ActionScheduler, SchedulerStats
from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.core.events import Event, EventBus, EventType
from uga.core.run_context import RunContext, RunStamp
from uga.environment.adapter import EnvironmentAdapter
from uga.gui.controller import GuiActionController, GuiActionSubmission
from uga.observation.buffer import TemporalObservationBuffer
from uga.observation.builder import ObservationBuilder
from uga.observation.schema import Observation
from uga.perception.builder import PerceptionBuilder
from uga.perception.schema import DecisionKind, NormalizedBox, PerceptionSnapshot, PlannerOutcome
from uga.policy.call_budget import CallBudget, DeadlineWorker
from uga.policy.chunk_controller import ActionChunkController, ActionChunkSubmission
from uga.policy.fast_policy import FastPolicy, FastPolicyOutput, PolicyContext
from uga.policy.vision_transport import ProviderError, ProviderErrorKind
from uga.recording.episode_writer import EpisodeWriter
from uga.safety.action_gate import generations_consistent, point_is_clickable, resolved_click_point
from uga.safety.sensitive_page import inspect_sensitive_page
from uga.time.clock import ClockBackend
from uga.windows.coordinates import CoordinateTransform, Rect


class CaptureSource(Protocol):
    async def capture_once(self) -> SequencedFrame: ...


def _physical_pointer_point(
    physical_actions: tuple[PhysicalAction, ...],
) -> tuple[float, float] | None:
    """The physical screen pixel of the pointer move inside a submission."""
    for item in physical_actions:
        if isinstance(item, AbsolutePointerAction):
            return (float(item.x), float(item.y))
    return None


@runtime_checkable
class ContinuousCaptureSource(CaptureSource, Protocol):
    records_frames: bool

    async def run(self, stop: asyncio.Event) -> None: ...


class GroundedPlanner(Protocol):
    @property
    def policy_version(self) -> str: ...

    def decide(
        self,
        *,
        snapshot: PerceptionSnapshot,
        frames: tuple[Frame, ...],
        goal: str,
        high_resolution_retry: bool = False,
        preferred_action_available: bool = True,
        session_context: str | None = None,
        quest_target_level: int | None = None,
        quest_text: str | None = None,
    ) -> PlannerOutcome: ...


@dataclass(frozen=True, slots=True)
class AgentLoopStep:
    observation: Observation
    mode_transition: ModeTransition | None
    policy_output: FastPolicyOutput | None
    submission: ActionChunkSubmission | None
    scheduler_stats: SchedulerStats
    perception: PerceptionSnapshot | None = None
    planner_outcome: PlannerOutcome | None = None
    gui_submission: GuiActionSubmission | None = None
    supervision: SupervisedDecision | None = None


class RealtimeAgentLoop:
    """One modular-monolith control loop with a one-item realtime queue."""

    def __init__(
        self,
        *,
        clock: ClockBackend,
        capture: CaptureSource,
        frames: FrameRingBuffer,
        observation_builder: ObservationBuilder,
        observations: TemporalObservationBuffer,
        environment: EnvironmentAdapter,
        mode_classifier: ModeClassifier,
        mode_router: ModeRouter,
        policy: FastPolicy | None,
        leases: ControlLeaseManager,
        controller: ActionChunkController,
        scheduler: ActionScheduler,
        events: EventBus,
        recorder: EpisodeWriter | None = None,
        grounded_planner: GroundedPlanner | None = None,
        perception_builder: PerceptionBuilder | None = None,
        closed_loop: ClosedLoopSupervisor | None = None,
        gui_controller: GuiActionController | None = None,
        key_resolver: KeyResolver | None = None,
        grounded_decision_interval_s: float = 0.0,
        dialogue_decision_interval_s: float = 1.0,
        continuous_grounded: bool = False,
        closed_loop_factory: Callable[[], ClosedLoopSupervisor] | None = None,
        run_context: RunContext | None = None,
        control_heartbeat: Callable[[], object] | None = None,
        recovery_budget: RecoveryBudget | None = None,
        max_continuous_rebuilds: int = 100,
        max_planner_failures: int = 5,
        decision_timeout_s: float = 60.0,
        perception_timeout_s: float = 10.0,
    ) -> None:
        grounded_parts = (
            grounded_planner,
            perception_builder,
            closed_loop,
            gui_controller,
            key_resolver,
        )
        if policy is None and grounded_planner is None:
            raise ContractViolation("agent loop requires a fast or grounded policy")
        if grounded_decision_interval_s < 0:
            raise ContractViolation("grounded decision interval cannot be negative")
        if dialogue_decision_interval_s < 0:
            raise ContractViolation("dialogue decision interval cannot be negative")
        if continuous_grounded and closed_loop_factory is None:
            raise ContractViolation("continuous grounded mode requires a closed-loop factory")
        if closed_loop_factory is not None and closed_loop is None:
            raise ContractViolation("closed-loop factory requires a grounded closed loop")
        if any(item is not None for item in grounded_parts) and not all(
            item is not None for item in grounded_parts
        ):
            raise ContractViolation("grounded loop components must be configured together")
        for timeout_s in (decision_timeout_s, perception_timeout_s):
            if not math.isfinite(timeout_s) or timeout_s <= 0:
                raise ContractViolation("worker deadlines must be finite and positive")
        self._decision_timeout_s = decision_timeout_s
        self._perception_timeout_s = perception_timeout_s
        self._model_worker = DeadlineWorker("uga-model")
        self._perception_worker = DeadlineWorker("uga-perception")
        self._supervision_worker = DeadlineWorker("uga-supervision")
        self._clock = clock
        self._capture = capture
        self._frames = frames
        self._observation_builder = observation_builder
        self._observations = observations
        self._environment = environment
        self._mode_classifier = mode_classifier
        self._mode_router = mode_router
        self._policy = policy
        self._leases = leases
        self._controller = controller
        self._scheduler = scheduler
        self._events = events
        self._recorder = recorder
        self._grounded_planner = grounded_planner
        self._perception_builder = perception_builder
        self._closed_loop = closed_loop
        self._gui_controller = gui_controller
        self._key_resolver = key_resolver
        self._grounded_decision_interval_ns = round(
            grounded_decision_interval_s * 1_000_000_000
        )
        self._dialogue_decision_interval_ns = round(
            dialogue_decision_interval_s * 1_000_000_000
        )
        self._continuous_grounded = continuous_grounded
        self._closed_loop_factory = closed_loop_factory
        self._run_context = run_context
        self._control_heartbeat = control_heartbeat
        self._recovery_budget = recovery_budget
        self._max_continuous_rebuilds = max_continuous_rebuilds
        self._continuous_rebuilds = 0
        self._next_grounded_inference_ns = 0
        self._grounded_failure_backoff_ns = max(
            self._grounded_decision_interval_ns, 15_000_000_000
        )
        if type(max_planner_failures) is not int or max_planner_failures < 1:
            raise ContractViolation("planner failure budget must be a positive integer")
        self._max_planner_failures = max_planner_failures
        self._consecutive_planner_failures = 0
        self._planner_failure_count = 0
        self._safety_discards = 0
        self._last_planner_error: str | None = None
        self._last_supervision_disposition: str | None = None
        self._last_supervision_reason: str | None = None
        self._last_planner_input_frame_id: str | None = None
        self._last_planner_input_age_ns: int | None = None
        self._continuous_cycle_count = 1
        self._task_generation = 1
        self._geometry_generation = 0
        self._geometry_key: tuple[object, ...] | None = None
        self._slot = LatestFrameSlot()
        if recorder is not None:
            events.subscribe(self._record_event)

    @property
    def dropped_policy_frames(self) -> int:
        return self._slot.replaced

    @property
    def terminal_status(self) -> TerminalStatus:
        return (
            TerminalStatus.RUNNING
            if self._closed_loop is None
            else self._closed_loop.status
        )

    @property
    def termination_reason(self) -> str | None:
        return None if self._closed_loop is None else self._closed_loop.termination_reason

    @property
    def goal_confidence(self) -> float | None:
        return None if self._closed_loop is None else self._closed_loop.goal_confidence

    @property
    def closed_loop_diagnostics(self) -> dict[str, object] | None:
        if self._closed_loop is None:
            return None
        diagnostics = self._closed_loop.diagnostics()
        diagnostics.update(
            {
                "continuous_mode": self._continuous_grounded,
                "continuous_cycle_count": self._continuous_cycle_count,
                "planner_failure_count": self._planner_failure_count,
                "last_planner_error": self._last_planner_error,
                "last_supervision_disposition": self._last_supervision_disposition,
                "last_supervision_reason": self._last_supervision_reason,
                "last_planner_input_frame_id": self._last_planner_input_frame_id,
                "last_planner_input_age_ns": self._last_planner_input_age_ns,
                "safety_discarded_results": self._safety_discards,
            }
        )
        return diagnostics

    def fail_closed_loop(self, reason: str) -> None:
        if self._closed_loop is not None and not self._closed_loop.is_terminal:
            self._closed_loop.fail(reason)

    def _run_live(self, stamp: RunStamp | None) -> bool:
        if self._run_context is None or stamp is None:
            return True
        return self._run_context.is_live(stamp)

    async def _discard_stale_result(
        self, observation: Observation, *, policy: str, phase: str
    ) -> None:
        # A latched stop or a generation advance arrived while the request was
        # in flight: the late result is recorded and dropped, never executed.
        self._safety_discards += 1
        await self._events.publish(
            EventType.POLICY_INFERENCE_COMPLETED,
            "agent.loop",
            {
                "observation_id": observation.observation_id,
                "policy": policy,
                "disposition": "discarded_stale",
                "reason": f"run stopped or generation advanced while {phase}",
            },
        )

    def drain_execution_receipts(self) -> None:
        """One receipt pump for observation, error teardown and finalization."""
        receipts = self._scheduler.drain_receipts()
        if self._recorder is not None:
            self._recorder.record_execution_receipts(receipts)
        if self._closed_loop is not None:
            self._closed_loop.record_execution_receipts(receipts)

    def _pre_action_observation(self, item: SequencedFrame) -> Observation:
        history = tuple(
            value.frame for value in self._frames.snapshot() if value.sequence <= item.sequence
        )
        if not history or history[-1] != item.frame:
            history = (item.frame,)
        observed = self._environment.enrich_observation(self._observation_builder.build(
            item.frame, history, self._mode_router.current, self._leases.current()
        ))
        if self._recorder is not None:
            self._recorder.record_observation(
                observed.observation_id, observed.created_at, observed.to_envelope()
            )
        return observed

    def _execution_is_current(
        self, stamp: RunStamp | None, validated: Frame, task_generation: int,
        *, visual_stability: bool = False,
    ) -> bool:
        if not self._run_live(stamp):
            return False
        supervisor = self._closed_loop
        if (
            supervisor is not None
            and supervisor.session is not None
            and supervisor.session.task_generation != task_generation
        ):
            return False
        current = self._frames.latest()
        if current is None:
            return False
        if supervisor is not None:
            if not supervisor.validate_execution_context(validated, current.frame)[0]:
                return False
            return not visual_stability or not ActionValidator._target_changed(
                NormalizedBox(0, 0, 1, 1), validated, current.frame
            )
        age = self._clock.now().value_ns - current.frame.capture_timestamp.value_ns
        return (
            0 <= age <= 1_000_000_000
            and current.frame.window_identity == validated.window_identity
            and current.frame.physical_rect == validated.physical_rect
        )

    async def step(self) -> AgentLoopStep:
        item = await self._capture.capture_once()
        latest_item = self._frames.latest()
        if latest_item is not None and latest_item.sequence > item.sequence:
            item = latest_item
        self._slot.offer(item)
        pending = self._slot.take()
        if pending is None:
            raise ContractViolation("realtime frame slot was unexpectedly empty")
        source_records_frames = isinstance(self._capture, ContinuousCaptureSource) and (
            self._capture.records_frames
        )
        if self._recorder is not None and not source_records_frames:
            self._recorder.record_frame(pending.frame)
        # Drain exactly once into supervision and recording. The observation
        # built below is post-action effect evidence, not a policy input.
        self.drain_execution_receipts()

        history_items = tuple(
            value for value in self._frames.snapshot() if value.sequence <= pending.sequence
        )
        history = tuple(value.frame for value in history_items)
        lease = self._leases.current()
        if self._closed_loop is not None and self._closed_loop.session is not None:
            # F08: the session's task generation is the single source of
            # truth — a quest identity change advances it and every request
            # or outcome stamped with the old generation is stale on arrival.
            self._task_generation = self._closed_loop.session.task_generation
        perception: PerceptionSnapshot | None = None
        effect_pending = False
        if self._perception_builder is not None:
            geometry_generation = self._update_geometry_generation(pending.frame)
            perception = await self._perception_worker.call(partial(
                self._perception_builder.build,
                pending,
                self._mode_router.current,
                geometry_generation=geometry_generation,
                task_generation=self._task_generation,
            ), CallBudget(self._perception_timeout_s))
            assert self._closed_loop is not None
            effect_pending = self._closed_loop.observe(perception, pending.frame).pending
            # observe() may commit a new task. Stamp the request AFTER that
            # update rather than one iteration late.
            if self._closed_loop.session is not None:
                self._task_generation = self._closed_loop.session.task_generation
                perception = replace(perception, task_generation=self._task_generation)
        observation = self._observation_builder.build(
            pending.frame,
            history,
            self._mode_router.current,
            lease,
            visible_text=None if perception is None else perception.text,
            belief_confidence=None if perception is None else perception.confidence,
            gui_state=(
                ()
                if perception is None
                else tuple((f"text_{index}", text) for index, text in enumerate(perception.text))
            ),
            progress_state=(
                ()
                if perception is None
                else (("state_signature", perception.state_signature),)
            ),
        )
        observation = self._environment.enrich_observation(observation)
        self._observations.append(observation)
        if self._recorder is not None:
            self._recorder.record_observation(
                observation.observation_id, observation.created_at, observation.to_envelope()
            )
        await self._events.publish(
            EventType.OBSERVATION_BUILT,
            "agent.loop",
            {
                "observation_id": observation.observation_id,
                "frame_id": pending.frame.frame_id,
                "capture_age_ns": observation.latency_context.capture_age_ns,
            },
        )

        evidence = self._mode_classifier.classify(observation)
        await self._events.publish(
            EventType.MODE_CANDIDATE,
            "agent.loop",
            {"mode": evidence.mode.value, "confidence": evidence.confidence},
        )
        transition = self._mode_router.consider(evidence)
        if transition is not None:
            self._leases.revoke_all()
            self._scheduler.flush()
            await self._events.publish(
                EventType.MODE_CHANGED,
                "agent.loop",
                {"previous": transition.previous.value, "mode": transition.current.value},
            )

        if perception is not None:
            sensitive = inspect_sensitive_page(perception.visible_text)
            if sensitive.requires_owner:
                self._leases.revoke_all()
                self._scheduler.neutralize()
                self._last_supervision_disposition = DecisionDisposition.WAIT.value
                self._last_supervision_reason = sensitive.reason
                await self._events.publish(
                    EventType.POLICY_INFERENCE_COMPLETED, "agent.loop",
                    {"disposition": "owner_required", "reason": sensitive.reason},
                )
                return AgentLoopStep(
                    observation, transition, None, None, self._scheduler.stats(), perception
                )

        output: FastPolicyOutput | None = None
        submission: ActionChunkSubmission | None = None
        planner_outcome: PlannerOutcome | None = None
        gui_submission: GuiActionSubmission | None = None
        supervision: SupervisedDecision | None = None
        grounded_planner = self._grounded_planner
        closed_loop = self._closed_loop
        if (
            grounded_planner is not None
            and closed_loop is not None
            and perception is not None
            and not effect_pending
            and not closed_loop.is_terminal
            and transition is None
            and self._clock.now().value_ns >= self._next_grounded_inference_ns
        ):
            await self._events.publish(
                EventType.POLICY_INFERENCE_STARTED,
                "agent.loop",
                {"observation_id": observation.observation_id, "policy": "grounded_vlm"},
            )
            self._last_planner_input_frame_id = perception.frame_id
            self._last_planner_input_age_ns = max(
                0,
                self._clock.now().value_ns - perception.captured_at.value_ns,
            )
            # Stamp the request generation: a stop or supervisor rebuild that
            # lands while the model thinks must kill this result on arrival.
            stamp = self._run_context.stamp() if self._run_context is not None else None
            budget = CallBudget(self._decision_timeout_s)
            try:
                session = closed_loop.session
                restored_unverified = (
                    session is not None and session.restored_task_unverified
                )
                outcome = await self._model_worker.call(partial(
                    grounded_planner.decide,
                    snapshot=perception,
                    frames=history[-3:],
                    goal=observation.user_goal,
                    high_resolution_retry=closed_loop.high_resolution_retry,
                    preferred_action_available=closed_loop.preferred_action_available,
                    session_context=(
                        None if session is None else session.context_summary()
                    ),
                    quest_target_level=(
                        None
                        if session is None
                        or session.latest_main_task is None
                        or restored_unverified
                        else quest_level_target(session.latest_main_task.raw_text)
                    ),
                    quest_text=(
                        None
                        if session is None
                        or session.latest_main_task is None
                        or restored_unverified
                        else session.latest_main_task.raw_text
                    ),
                ), budget)
            except BackendUnavailableError as exc:
                self._planner_failure_count += 1
                self._consecutive_planner_failures += 1
                self._last_planner_error = str(exc)
                if isinstance(exc, ProviderError) and exc.fatal:
                    self._leases.revoke_all()
                    self._scheduler.neutralize()
                    raise
                if self._consecutive_planner_failures >= self._max_planner_failures:
                    self._leases.revoke_all()
                    self._scheduler.neutralize()
                    raise ProviderError(
                        ProviderErrorKind.UNREACHABLE,
                        "planner retry budget exhausted", fatal=True,
                    ) from exc
                retry_ns = self._grounded_failure_backoff_ns
                if isinstance(exc, ProviderError) and exc.retry_after_s is not None:
                    delay = exc.retry_after_s
                    if math.isfinite(delay) and delay > 300.0:
                        raise ProviderError(
                            ProviderErrorKind.RATE_LIMIT,
                            "provider retry delay exceeds the run retry budget", fatal=True,
                        ) from exc
                    if math.isfinite(delay) and delay > 0.0:
                        retry_ns = max(retry_ns, round(delay * 1_000_000_000))
                self._next_grounded_inference_ns = self._clock.now().value_ns + retry_ns
                await self._events.publish(
                    EventType.POLICY_INFERENCE_COMPLETED,
                    "agent.loop",
                    {
                        "observation_id": observation.observation_id,
                        "policy": "grounded_vlm",
                        "disposition": "retry",
                        "reason": self._last_planner_error,
                        "retry_after_ns": retry_ns,
                    },
                )
                stats = self._scheduler.tick()
                return AgentLoopStep(
                    observation,
                    transition,
                    None,
                    None,
                    stats,
                    perception,
                )
            if not self._run_live(stamp):
                await self._discard_stale_result(
                    observation, policy="grounded_vlm", phase="model inference was in flight"
                )
                return AgentLoopStep(
                    observation,
                    transition,
                    None,
                    None,
                    self._scheduler.tick(),
                    perception,
                )
            self._last_planner_error = None
            self._consecutive_planner_failures = 0
            planner_outcome = outcome
            dialogue_cadence = (
                outcome.kind == DecisionKind.ACT
                and outcome.action is not None
                and outcome.action.target_label in {"对话继续", "5秒后自动继续"}
            )
            self._next_grounded_inference_ns = self._clock.now().value_ns + (
                self._dialogue_decision_interval_ns
                if dialogue_cadence
                else self._grounded_decision_interval_ns
            )
            latest_after_inference = self._frames.latest() or pending
            if latest_after_inference.sequence == pending.sequence:
                fresh_perception = perception
            else:
                assert self._perception_builder is not None
                geometry_generation = self._update_geometry_generation(
                    latest_after_inference.frame
                )
                fresh_perception = await self._perception_worker.call(partial(
                    self._perception_builder.build,
                    latest_after_inference,
                    self._mode_router.current,
                    geometry_generation=geometry_generation,
                    task_generation=self._task_generation,
                ), budget)
            if closed_loop.session is not None:
                # A task can change while the model thinks. Incorporate the
                # fresh screen before comparing with the original request.
                closed_loop.session.observe_snapshot(
                    fresh_perception, fresh_perception.captured_at.value_ns
                )
                self._task_generation = closed_loop.session.task_generation
                fresh_perception = replace(
                    fresh_perception, task_generation=self._task_generation
                )
            supervised = await self._supervision_worker.call(partial(
                closed_loop.assess,
                outcome,
                perception,
                fresh_perception,
                pending.frame,
                latest_after_inference.frame,
                observation.user_goal,
                decision_source=getattr(grounded_planner, "last_decision_source", None),
            ), budget)
            if not self._run_live(stamp):
                await self._discard_stale_result(
                    observation, policy="grounded_vlm", phase="supervision was in flight"
                )
                return AgentLoopStep(
                    observation,
                    transition,
                    None,
                    None,
                    self._scheduler.tick(),
                    perception,
                    planner_outcome,
                )
            # assess() may replace the proposed action (model exit intent
            # routed to the calibrated exit hotspots): every downstream
            # consumer must act on the SUPERVISED outcome.  Submitting the
            # raw planner proposal once sent a routed exit's original box —
            # parked on MuMu's own title-bar close button — to SendInput.
            outcome = supervised.outcome
            execution_item = latest_after_inference
            if supervised.disposition in {DecisionDisposition.EXECUTE, DecisionDisposition.RECOVER}:
                current_item = self._frames.latest() or latest_after_inference
                final_perception = fresh_perception
                if current_item.sequence != latest_after_inference.sequence:
                    assert self._perception_builder is not None
                    final_perception = await self._perception_worker.call(partial(
                        self._perception_builder.build, current_item, self._mode_router.current,
                        geometry_generation=self._update_geometry_generation(current_item.frame),
                        task_generation=self._task_generation,
                    ), budget)
                    if closed_loop.session is not None:
                        closed_loop.session.observe_snapshot(
                            final_perception, final_perception.captured_at.value_ns
                        )
                        self._task_generation = closed_loop.session.task_generation
                        final_perception = replace(
                            final_perception, task_generation=self._task_generation
                        )
                execution_fresh, execution_reason = generations_consistent(
                    outcome, perception, final_perception
                )
                sensitive = inspect_sensitive_page(final_perception.visible_text, outcome.action)
                if sensitive.requires_owner:
                    execution_fresh, execution_reason = False, sensitive.reason
                if (execution_fresh and outcome.action is not None
                        and supervised.disposition == DecisionDisposition.EXECUTE):
                    if outcome.action.target_box is not None:
                        verdict = point_is_clickable(
                            resolved_click_point(outcome.action),
                            no_click_regions=closed_loop.no_click_regions,
                            decided_text=perception.visible_text,
                            fresh_text=final_perception.visible_text,
                        )
                        execution_fresh, execution_reason = verdict.allowed, verdict.reason
                    if execution_fresh:
                        # Dynamic pixels need a match from the NEW observation,
                        # not an OCR match obtained before the slow verifier.
                        grounding = ActionValidator._ocr_target_grounding(
                            outcome.action, final_perception
                        )
                        execution_fresh, execution_reason = closed_loop.validate_execution_frame(
                            outcome, latest_after_inference.frame, current_item.frame,
                            target_was_ocr_grounded=grounding == "match",
                        )
                elif execution_fresh:
                    execution_fresh, execution_reason = closed_loop.validate_execution_context(
                        latest_after_inference.frame, current_item.frame
                    )
                if not execution_fresh:
                    supervised = SupervisedDecision(
                        DecisionDisposition.WAIT if sensitive.requires_owner
                        else DecisionDisposition.REOBSERVE, execution_reason, outcome,
                    )
                else:
                    execution_item = current_item
                    latest_after_inference = current_item
                    fresh_perception = final_perception
            supervision = supervised
            self._last_supervision_disposition = supervised.disposition.value
            self._last_supervision_reason = supervised.reason
            await self._events.publish(
                EventType.POLICY_INFERENCE_COMPLETED,
                "agent.loop",
                {
                    "observation_id": observation.observation_id,
                    "decision_id": outcome.decision_id,
                    "kind": outcome.kind.value,
                    "confidence": outcome.confidence,
                    "disposition": supervised.disposition.value,
                    "reason": supervised.reason,
                },
            )
            if self._recorder is not None:
                self._recorder.record_planner(
                    outcome.decision_id,
                    self._clock.now(),
                    {
                        "outcome": outcome.to_envelope(),
                        "disposition": supervised.disposition.value,
                        "supervision_reason": supervised.reason,
                        "source_frame_age_ns": max(
                            0,
                            execution_item.frame.capture_timestamp.value_ns
                            - perception.captured_at.value_ns,
                        ),
                        "effect_observed": closed_loop.last_effect_observed,
                        "expected_effect": (
                            None
                            if outcome.action is None
                            else outcome.action.expected_effect
                        ),
                        "schema_valid": getattr(
                            grounded_planner, "last_schema_valid", None
                        ),
                        "model_raw_output": getattr(
                            grounded_planner, "last_raw_reply", None
                        ),
                        "verifier_conclusion": supervised.reason,
                        "recovery_count": closed_loop.recovery_count,
                        "loop_signature": (
                            None
                            if closed_loop.last_loop_finding is None
                            else {
                                "kind": closed_loop.last_loop_finding.kind.value,
                                "cycle_length": closed_loop.last_loop_finding.cycle_length,
                                "detail": closed_loop.last_loop_finding.detail,
                            }
                        ),
                    },
                )
            if supervised.disposition == DecisionDisposition.EXECUTE:
                if not self._run_live(stamp):
                    await self._discard_stale_result(
                        observation,
                        policy="grounded_vlm",
                        phase="execution was about to be authorized",
                    )
                    return AgentLoopStep(
                        observation,
                        transition,
                        None,
                        None,
                        self._scheduler.tick(),
                        perception,
                        planner_outcome,
                        gui_submission,
                        supervision,
                    )
                assert self._gui_controller is not None
                assert self._key_resolver is not None
                pre_action = self._pre_action_observation(execution_item)
                gui_action = closed_loop.to_gui_action(outcome, self._key_resolver)
                now = self._clock.now()
                gui_lease = self._leases.grant(
                    ControlOwner.GUI_AGENT,
                    ControlMode.GUI,
                    gui_action.lifetime.expires_at.value_ns - now.value_ns,
                    confidence=gui_action.confidence,
                    reason=f"grounded GUI decision {outcome.decision_id}",
                )
                transform = self._coordinate_transform(execution_item.frame)
                gui_submission = self._gui_controller.submit(
                    gui_action,
                    transform,
                    execution_item.frame.window_identity,
                    gui_lease,
                    observation_id=observation.observation_id,
                    policy_version=grounded_planner.policy_version,
                    pre_action_observation_id=pre_action.observation_id,
                    pre_action_capture_ns=pre_action.latest_frame.capture_timestamp.value_ns,
                    execution_guard=lambda: self._execution_is_current(
                        stamp, execution_item.frame, outcome.task_generation, visual_stability=True
                    ),
                )
                if gui_submission.decision is not None:
                    await self._publish_decision(gui_submission.decision)
                    if gui_submission.decision.accepted:
                        closed_loop.start_action(
                            outcome,
                            fresh_perception,
                            execution_item.frame,
                            source=getattr(
                                grounded_planner, "last_decision_source", None
                            ),
                            physical_point=_physical_pointer_point(
                                gui_submission.physical_actions
                            ),
                            primitives=tuple(
                                type(item).__name__
                                for item in gui_submission.physical_actions
                            ),
                            submitted_action_ids=frozenset(
                                item.action_id
                                for item in gui_submission.physical_actions
                            ),
                            expected_primitives=len(gui_submission.physical_actions),
                        )
                    else:
                        closed_loop.fail("grounded GUI proposal was rejected")
            elif supervised.disposition == DecisionDisposition.RECOVER:
                if not self._run_live(stamp):
                    await self._discard_stale_result(
                        observation,
                        policy="grounded_vlm",
                        phase="recovery was about to be authorized",
                    )
                    return AgentLoopStep(
                        observation,
                        transition,
                        None,
                        None,
                        self._scheduler.tick(),
                        perception,
                        planner_outcome,
                        gui_submission,
                        supervision,
                    )
                assert supervised.recovery is not None
                assert self._gui_controller is not None
                assert self._key_resolver is not None
                recovery_item = self._frames.latest() or latest_after_inference
                recovery_context_stable, recovery_context_reason = (
                    closed_loop.validate_execution_context(
                        latest_after_inference.frame, recovery_item.frame
                    )
                )
                if not recovery_context_stable:
                    # A recreated or resized window must never receive the
                    # recovery exit click: drop this attempt and re-observe;
                    # the pending recovery stays armed for the next cycle.
                    await self._events.publish(
                        EventType.POLICY_INFERENCE_COMPLETED,
                        "agent.loop",
                        {
                            "observation_id": observation.observation_id,
                            "disposition": "recovery_discarded_stale",
                            "reason": recovery_context_reason,
                        },
                    )
                    return AgentLoopStep(
                        observation,
                        transition,
                        None,
                        None,
                        self._scheduler.tick(),
                        perception,
                        planner_outcome,
                        gui_submission,
                        supervision,
                    )
                pre_action = self._pre_action_observation(recovery_item)
                recovery_action = closed_loop.to_recovery_gui_action(
                    supervised.recovery, self._key_resolver, snapshot=fresh_perception
                )
                now = self._clock.now()
                recovery_lease = self._leases.grant(
                    ControlOwner.GUI_AGENT,
                    ControlMode.GUI,
                    recovery_action.lifetime.expires_at.value_ns - now.value_ns,
                    confidence=recovery_action.confidence,
                    reason=f"closed-loop recovery {supervised.recovery.value}",
                )
                gui_submission = self._gui_controller.submit(
                    recovery_action,
                    self._coordinate_transform(latest_after_inference.frame),
                    latest_after_inference.frame.window_identity,
                    recovery_lease,
                    observation_id=observation.observation_id,
                    policy_version="closed-loop-recovery-1.0.0",
                    pre_action_observation_id=pre_action.observation_id,
                    pre_action_capture_ns=pre_action.latest_frame.capture_timestamp.value_ns,
                    execution_guard=lambda: self._execution_is_current(
                        stamp, recovery_item.frame, fresh_perception.task_generation,
                        visual_stability=True
                    ),
                )
                if gui_submission.decision is not None:
                    await self._publish_decision(gui_submission.decision)
                    if gui_submission.decision.accepted:
                        closed_loop.start_recovery_action(
                            fresh_perception,
                            latest_after_inference.frame,
                            action_id=recovery_action.action_id,
                            physical_point=_physical_pointer_point(
                                gui_submission.physical_actions
                            ),
                            primitives=tuple(
                                type(item).__name__
                                for item in gui_submission.physical_actions
                            ),
                            submitted_action_ids=frozenset(
                                item.action_id
                                for item in gui_submission.physical_actions
                            ),
                            expected_primitives=len(gui_submission.physical_actions),
                        )
                    else:
                        closed_loop.fail("recovery GUI proposal was rejected")
            stats = self._scheduler.tick()
            return AgentLoopStep(
                observation,
                transition,
                None,
                None,
                stats,
                perception,
                planner_outcome,
                gui_submission,
                supervision,
            )
        if (
            self._policy is not None
            and self._mode_router.current == ControlMode.PLAY_3D
            and transition is None
        ):
            await self._events.publish(
                EventType.POLICY_INFERENCE_STARTED,
                "agent.loop",
                {"observation_id": observation.observation_id},
            )
            context = PolicyContext(
                observation.observation_id,
                self._clock.now(),
                self._observations.context(),
                observation.user_goal,
            )
            stamp = self._run_context.stamp() if self._run_context is not None else None
            output = await self._model_worker.call(
                partial(self._policy.infer, context), CallBudget(self._decision_timeout_s)
            )
            await self._events.publish(
                EventType.POLICY_INFERENCE_COMPLETED,
                "agent.loop",
                {
                    "observation_id": observation.observation_id,
                    "chunk_id": output.chunk.chunk_id,
                    "confidence": output.chunk.confidence,
                    "need_reasoning": output.need_reasoning,
                },
            )
            if output.need_reasoning:
                self._leases.revoke_all()
                self._scheduler.flush()
                await self._events.publish(
                    EventType.REASONING_REQUESTED,
                    "agent.loop",
                    {
                        "observation_id": observation.observation_id,
                        "reason": output.reasoning_reason or "policy_request",
                    },
                )
                return AgentLoopStep(
                    observation,
                    transition,
                    output,
                    None,
                    self._scheduler.tick(),
                )
            if not self._run_live(stamp):
                await self._discard_stale_result(
                    observation, policy="fast_policy", phase="policy inference was in flight"
                )
                return AgentLoopStep(
                    observation,
                    transition,
                    output,
                    None,
                    self._scheduler.tick(),
                )
            now = self._clock.now()
            remaining_ns = output.chunk.expires_at.value_ns - now.value_ns
            if remaining_ns <= 0:
                raise ContractViolation("Fast Policy returned an already-expired action chunk")
            lease = self._leases.grant(
                ControlOwner.FAST_POLICY,
                ControlMode.PLAY_3D,
                remaining_ns,
                confidence=output.chunk.confidence,
                reason=f"Fast Policy chunk {output.chunk.chunk_id}",
            )
            await self._events.publish(
                EventType.LEASE_GRANTED,
                "agent.loop",
                {"lease_id": lease.lease_id, "owner": lease.owner.name},
            )
            await self._events.publish(
                EventType.ACTION_PROPOSED,
                "agent.loop",
                {"chunk_id": output.chunk.chunk_id, "horizon": output.chunk.horizon},
            )
            pre_action = self._pre_action_observation(pending)
            submission = self._controller.submit(
                output.chunk, pending.frame.window_identity, lease,
                pre_action_observation_id=pre_action.observation_id,
                pre_action_capture_ns=pre_action.latest_frame.capture_timestamp.value_ns,
                execution_guard=lambda: self._execution_is_current(
                    stamp, pending.frame, self._task_generation
                ),
            )
            await self._publish_decision(submission.decision)

        stats = self._scheduler.tick()
        return AgentLoopStep(
            observation,
            transition,
            output,
            submission,
            stats,
            perception,
            planner_outcome,
            gui_submission,
            supervision,
        )

    async def run(
        self,
        stop: asyncio.Event,
        *,
        observation_hz: float = 5.0,
        scheduler_hz: float = ActionScheduler.DEFAULT_HZ,
    ) -> None:
        """Run observation and scheduler latency domains until explicitly stopped."""
        if observation_hz <= 0 or scheduler_hz <= 0:
            raise ContractViolation("agent-loop frequencies must be positive")

        async def observe() -> None:
            period_s = 1.0 / observation_hz
            while not stop.is_set():
                await self.step()
                if self._closed_loop is not None and self._closed_loop.is_terminal:
                    if not self._continuous_grounded:
                        stop.set()
                        break
                    if self._run_context is not None and self._run_context.should_stop():
                        # A latched safety stop is never auto-cleared by a
                        # supervisor rebuild; the run ends here.
                        stop.set()
                        break
                    previous = self._closed_loop
                    if previous.status is TerminalStatus.BLOCKED:
                        # A terminal refusal is not permission to rebuild a
                        # supervisor and try the forbidden operation again.
                        stop.set()
                        break
                    if (
                        self._recovery_budget is not None
                        and self._recovery_budget.exhausted
                        and previous.status is not TerminalStatus.SUCCEEDED
                    ):
                        # F06: a blocked run on an exhausted recovery budget
                        # would spin blocked-supervisor rebuilds at observation
                        # rate — stand down honestly instead.
                        stop.set()
                        break
                    if self._continuous_rebuilds >= self._max_continuous_rebuilds:
                        stop.set()
                        break
                    self._continuous_rebuilds += 1
                    assert self._closed_loop_factory is not None
                    self._closed_loop = self._closed_loop_factory()
                    # Outstanding requests from the previous cycle die with
                    # the old generation; they must not adopt the new one.
                    if self._run_context is not None:
                        self._run_context.advance_generation()
                    # RunContext owns cycle invalidation. Task identity is
                    # exclusively owned by GameSessionState, not by rebuilds.
                    self._continuous_cycle_count += 1
                    self._next_grounded_inference_ns = (
                        self._clock.now().value_ns + self._grounded_failure_backoff_ns
                    )
                    await self._events.publish(
                        EventType.AGENT_STUCK,
                        "agent.loop",
                        {
                            "status": previous.status.value,
                            "reason": previous.termination_reason or "closed_loop_terminal",
                            "disposition": "reset_and_continue",
                            "cycle": self._continuous_cycle_count,
                        },
                    )
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=period_s)

        try:
            async with asyncio.TaskGroup() as tasks:
                if isinstance(self._capture, ContinuousCaptureSource):
                    tasks.create_task(self._capture.run(stop))
                tasks.create_task(
                    self._scheduler.run(
                        stop, frequency_hz=scheduler_hz, heartbeat=self._control_heartbeat
                    )
                )
                observation_task = tasks.create_task(observe())

                async def cancel_on_stop() -> None:
                    await stop.wait()
                    if not observation_task.done():
                        observation_task.cancel()

                tasks.create_task(cancel_on_stop())
        finally:
            self._leases.revoke_all(notify=False)
            try:
                self._scheduler.neutralize()
            finally:
                self.drain_execution_receipts()

    def _update_geometry_generation(self, frame: Frame) -> int:
        key = (
            frame.window_identity,
            frame.width,
            frame.height,
            frame.client_rect,
            frame.physical_rect,
        )
        if key != self._geometry_key:
            self._geometry_key = key
            self._geometry_generation += 1
        return self._geometry_generation

    @staticmethod
    def _coordinate_transform(frame: Frame) -> CoordinateTransform:
        image = Rect(0, 0, frame.width, frame.height)
        return CoordinateTransform(
            image_rect=image,
            image_content_rect=frame.client_rect,
            client_screen_rect=frame.physical_rect,
            window_screen_rect=frame.physical_rect,
            dpi_scale=1.0,
        )

    async def _publish_decision(self, decision: ArbiterDecision) -> None:
        await self._events.publish(
            EventType.ACTION_ACCEPTED if decision.accepted else EventType.ACTION_REJECTED,
            "agent.loop",
            {
                "proposal_id": decision.proposal.proposal_id,
                "reason": decision.reason.value,
                "actions": len(decision.proposal.actions),
            },
        )

    def _record_event(self, event: Event) -> None:
        if self._recorder is not None:
            self._recorder.record_event(
                uuid.uuid4().hex,
                event.timestamp,
                {
                    "event_type": event.event_type.value,
                    "source": event.source,
                    "payload": event.payload,
                },
            )
