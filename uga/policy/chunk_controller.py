from __future__ import annotations

from dataclasses import dataclass

from uga.control.arbiter import ActionArbiter, ArbiterDecision
from uga.control.canonical import CanonicalAction
from uga.control.lease import ControlLease, ControlMode, ControlOwner
from uga.control.lifetime import ActionLifetime
from uga.control.proposal import ActionProposal
from uga.control.scheduler import ActionScheduler
from uga.core.errors import ContractViolation
from uga.environment.adapter import EnvironmentAdapter
from uga.policy.action_chunk import ActionButton, ActionChunk
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.schema import ActionProvenance
from uga.time.clock import UGATime
from uga.windows.window_identity import WindowIdentity

CanonicalButton = ActionButton


@dataclass(frozen=True, slots=True)
class ActionChunkSubmission:
    canonical_actions: tuple[CanonicalAction, ...]
    decision: ArbiterDecision
    scheduled_physical_actions: int


def expand_action_chunk(chunk: ActionChunk) -> tuple[CanonicalAction, ...]:
    chunk.validate()
    interval_ns = round(1_000_000_000 / chunk.tick_rate_hz)
    actions: list[CanonicalAction] = []
    for index in range(chunk.horizon):
        effective_ns = chunk.effective_from.value_ns + interval_ns * index
        if effective_ns >= chunk.expires_at.value_ns:
            break
        expires_ns = min(effective_ns + interval_ns, chunk.expires_at.value_ns)
        buttons = CanonicalButton(chunk.buttons[index])
        actions.append(
            CanonicalAction(
                f"{chunk.chunk_id}:tick:{index}",
                ActionLifetime(
                    chunk.generated_at,
                    UGATime(effective_ns),
                    UGATime(expires_ns),
                ),
                move_x=chunk.move_x[index],
                move_y=chunk.move_y[index],
                look_x=chunk.look_x[index],
                look_y=chunk.look_y[index],
                jump=bool(buttons & CanonicalButton.JUMP),
                sprint=bool(buttons & CanonicalButton.SPRINT),
                crouch=bool(buttons & CanonicalButton.CROUCH),
                interact=bool(buttons & CanonicalButton.INTERACT),
                primary=bool(buttons & CanonicalButton.PRIMARY),
                secondary=bool(buttons & CanonicalButton.SECONDARY),
                menu=bool(buttons & CanonicalButton.MENU),
                confirm=bool(buttons & CanonicalButton.CONFIRM),
                back=bool(buttons & CanonicalButton.BACK),
            )
        )
    if not actions:
        raise ContractViolation("action chunk has no executable ticks within its lifetime")
    return tuple(actions)


class ActionChunkController:
    """Routes Fast Policy chunks through environment, arbiter, and scheduler."""

    def __init__(
        self,
        environment: EnvironmentAdapter,
        arbiter: ActionArbiter,
        scheduler: ActionScheduler,
        recorder: EpisodeWriter | None = None,
    ) -> None:
        self._environment = environment
        self._arbiter = arbiter
        self._scheduler = scheduler
        self._recorder = recorder

    def submit(
        self,
        chunk: ActionChunk,
        target: WindowIdentity,
        lease: ControlLease,
    ) -> ActionChunkSubmission:
        if lease.owner != ControlOwner.FAST_POLICY or lease.mode != ControlMode.PLAY_3D:
            raise ContractViolation("Fast Policy chunk requires a PLAY_3D Fast Policy lease")
        canonical = expand_action_chunk(chunk)
        physical = tuple(
            action
            for canonical_action in canonical
            for action in self._environment.adapt_action(canonical_action)
        )
        if not physical:
            raise ContractViolation("action chunk produced no physical actions")
        proposal = ActionProposal(
            f"proposal:{chunk.chunk_id}",
            f"fast-policy:{chunk.policy_version}",
            lease.owner,
            lease.mode,
            lease.lease_id,
            lease.generation,
            ActionLifetime(chunk.generated_at, chunk.effective_from, chunk.expires_at),
            physical,
            chunk.observation_id,
            chunk.confidence,
        )
        decision = self._arbiter.decide(proposal)
        if decision.accepted and self._recorder is not None:
            for canonical_action in canonical:
                self._recorder.record_canonical_action(
                    canonical_action,
                    ActionProvenance(
                        canonical_action.action_id,
                        "FAST_POLICY",
                        chunk.policy_version,
                        None,
                        chunk.observation_id,
                        None,
                        None,
                        lease.mode.value,
                        lease.lease_id,
                        chunk.confidence,
                        False,
                        canonical_action.lifetime,
                    ),
                )
            for physical_action in physical:
                self._recorder.record_action(
                    physical_action,
                    ActionProvenance(
                        physical_action.action_id,
                        "FAST_POLICY",
                        chunk.policy_version,
                        None,
                        chunk.observation_id,
                        None,
                        None,
                        lease.mode.value,
                        lease.lease_id,
                        chunk.confidence,
                        False,
                        physical_action.lifetime,
                    ),
                )
        scheduled = self._scheduler.schedule(decision, target, lease)
        return ActionChunkSubmission(canonical, decision, scheduled)
