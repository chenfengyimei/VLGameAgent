from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
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
from uga.environment.adapter import EnvironmentAdapter
from uga.gui.controller import GuiActionController, GuiActionSubmission
from uga.gui.schema import GuiActionKind
from uga.observation.buffer import TemporalObservationBuffer
from uga.observation.builder import ObservationBuilder
from uga.observation.schema import Observation
from uga.perception.builder import PerceptionBuilder
from uga.perception.schema import DecisionKind, PerceptionSnapshot, PlannerOutcome
from uga.policy.chunk_controller import ActionChunkController, ActionChunkSubmission
from uga.policy.fast_policy import FastPolicy, FastPolicyOutput, PolicyContext
from uga.recording.episode_writer import EpisodeWriter
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
        self._next_grounded_inference_ns = 0
        self._grounded_failure_backoff_ns = max(
            self._grounded_decision_interval_ns, 15_000_000_000
        )
        self._planner_failure_count = 0
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
            }
        )
        return diagnostics

    def fail_closed_loop(self, reason: str) -> None:
        if self._closed_loop is not None and not self._closed_loop.is_terminal:
            self._closed_loop.fail(reason)

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

        history_items = tuple(
            value for value in self._frames.snapshot() if value.sequence <= pending.sequence
        )
        history = tuple(value.frame for value in history_items)
        lease = self._leases.current()
        perception: PerceptionSnapshot | None = None
        effect_pending = False
        if self._perception_builder is not None:
            geometry_generation = self._update_geometry_generation(pending.frame)
            perception = await asyncio.to_thread(
                self._perception_builder.build,
                pending,
                self._mode_router.current,
                geometry_generation=geometry_generation,
                task_generation=self._task_generation,
            )
            assert self._closed_loop is not None
            effect_pending = self._closed_loop.observe(perception, pending.frame).pending
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
            try:
                session = closed_loop.session
                outcome = await asyncio.to_thread(
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
                        if session is None or session.latest_main_task is None
                        else quest_level_target(session.latest_main_task.raw_text)
                    ),
                    quest_text=(
                        None
                        if session is None or session.latest_main_task is None
                        else session.latest_main_task.raw_text
                    ),
                )
            except BackendUnavailableError as exc:
                self._planner_failure_count += 1
                self._last_planner_error = str(exc)
                self._next_grounded_inference_ns = (
                    self._clock.now().value_ns + self._grounded_failure_backoff_ns
                )
                await self._events.publish(
                    EventType.POLICY_INFERENCE_COMPLETED,
                    "agent.loop",
                    {
                        "observation_id": observation.observation_id,
                        "policy": "grounded_vlm",
                        "disposition": "retry",
                        "reason": self._last_planner_error,
                        "retry_after_ns": self._grounded_failure_backoff_ns,
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
            self._last_planner_error = None
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
                fresh_perception = await asyncio.to_thread(
                    self._perception_builder.build,
                    latest_after_inference,
                    self._mode_router.current,
                    geometry_generation=geometry_generation,
                    task_generation=self._task_generation,
                )
            supervised = await asyncio.to_thread(
                closed_loop.assess,
                outcome,
                perception,
                fresh_perception,
                pending.frame,
                latest_after_inference.frame,
                observation.user_goal,
            )
            # assess() may replace the proposed action (model exit intent
            # routed to the calibrated exit hotspots): every downstream
            # consumer must act on the SUPERVISED outcome.  Submitting the
            # raw planner proposal once sent a routed exit's original box —
            # parked on MuMu's own title-bar close button — to SendInput.
            outcome = supervised.outcome
            execution_item = latest_after_inference
            if supervised.disposition == DecisionDisposition.EXECUTE:
                current_item = self._frames.latest() or latest_after_inference
                is_deterministic_exit = (
                    outcome.action is not None
                    and outcome.action.target_label
                    in {"ui_back", "ui_close", "ui_promote"}
                )
                if is_deterministic_exit:
                    # Calibrated hotspots and OCR-glyph closes carry no
                    # groundable text; the pixel-change freshness check would
                    # reject them forever on animated pages.
                    execution_fresh, execution_reason = True, "deterministic exit control"
                else:
                    grounding_match = (
                        outcome.action is not None
                        and ActionValidator._ocr_target_grounding(
                            outcome.action, fresh_perception
                        )
                        == "match"
                    )
                    decided_missing = (
                        outcome.action is not None
                        and ActionValidator._ocr_target_grounding(
                            outcome.action, perception
                        )
                        == "missing"
                    )
                    visual_only = (
                        not grounding_match
                        and decided_missing
                        and outcome.action is not None
                        and outcome.action.confidence >= 0.85
                        and outcome.action.kind == GuiActionKind.CLICK
                    )
                    execution_fresh, execution_reason = closed_loop.validate_execution_frame(
                        outcome,
                        latest_after_inference.frame,
                        current_item.frame,
                        # Graphical animated buttons (晋升 medallion) tolerate
                        # dynamic pixels exactly like OCR-grounded targets.
                        target_was_ocr_grounded=grounding_match or visual_only,
                    )
                if not execution_fresh:
                    supervised = SupervisedDecision(
                        DecisionDisposition.REOBSERVE,
                        execution_reason,
                        outcome,
                    )
                else:
                    execution_item = current_item
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
                assert self._gui_controller is not None
                assert self._key_resolver is not None
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
                        )
                    else:
                        closed_loop.fail("grounded GUI proposal was rejected")
            elif supervised.disposition == DecisionDisposition.RECOVER:
                assert supervised.recovery is not None
                assert self._gui_controller is not None
                assert self._key_resolver is not None
                recovery_action = closed_loop.to_recovery_gui_action(
                    supervised.recovery, self._key_resolver
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
            output = await asyncio.to_thread(self._policy.infer, context)
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
            submission = self._controller.submit(output.chunk, pending.frame.window_identity, lease)
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
                    previous = self._closed_loop
                    assert self._closed_loop_factory is not None
                    self._closed_loop = self._closed_loop_factory()
                    self._task_generation += 1
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
                tasks.create_task(self._scheduler.run(stop, frequency_hz=scheduler_hz))
                tasks.create_task(observe())
        finally:
            self._leases.revoke_all(notify=False)

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
