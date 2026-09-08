from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from typing import Protocol

from uga.agent.mode_router import ModeClassifier, ModeRouter, ModeTransition
from uga.capture.ring_buffer import FrameRingBuffer, LatestFrameSlot, SequencedFrame
from uga.control.arbiter import ArbiterDecision
from uga.control.lease import ControlMode, ControlOwner
from uga.control.lease_manager import ControlLeaseManager
from uga.control.scheduler import ActionScheduler, SchedulerStats
from uga.core.errors import ContractViolation
from uga.core.events import Event, EventBus, EventType
from uga.environment.adapter import EnvironmentAdapter
from uga.observation.buffer import TemporalObservationBuffer
from uga.observation.builder import ObservationBuilder
from uga.observation.schema import Observation
from uga.policy.chunk_controller import ActionChunkController, ActionChunkSubmission
from uga.policy.fast_policy import FastPolicy, FastPolicyOutput, PolicyContext
from uga.recording.episode_writer import EpisodeWriter
from uga.time.clock import ClockBackend


class CaptureSource(Protocol):
    async def capture_once(self) -> SequencedFrame: ...


@dataclass(frozen=True, slots=True)
class AgentLoopStep:
    observation: Observation
    mode_transition: ModeTransition | None
    policy_output: FastPolicyOutput | None
    submission: ActionChunkSubmission | None
    scheduler_stats: SchedulerStats


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
        policy: FastPolicy,
        leases: ControlLeaseManager,
        controller: ActionChunkController,
        scheduler: ActionScheduler,
        events: EventBus,
        recorder: EpisodeWriter | None = None,
    ) -> None:
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
        self._slot = LatestFrameSlot()
        if recorder is not None:
            events.subscribe(self._record_event)

    @property
    def dropped_policy_frames(self) -> int:
        return self._slot.replaced

    async def step(self) -> AgentLoopStep:
        item = await self._capture.capture_once()
        self._slot.offer(item)
        pending = self._slot.take()
        if pending is None:
            raise ContractViolation("realtime frame slot was unexpectedly empty")
        if self._recorder is not None:
            self._recorder.record_frame(pending.frame)

        history = tuple(value.frame for value in self._frames.snapshot())
        lease = self._leases.current()
        observation = self._observation_builder.build(
            pending.frame, history, self._mode_router.current, lease
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
        if self._mode_router.current == ControlMode.PLAY_3D and transition is None:
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
        return AgentLoopStep(observation, transition, output, submission, stats)

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
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=period_s)

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(self._scheduler.run(stop, frequency_hz=scheduler_hz))
            tasks.create_task(observe())

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
