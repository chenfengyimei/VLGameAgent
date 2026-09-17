from __future__ import annotations

from collections.abc import Callable
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
        pointer_x = chunk.pointer_x
        pointer_y = chunk.pointer_y
        pointer_down = False
        pointer_up = False
        if chunk.pointer_drag:
            # Drag chunks carry a pixel path pressed at the first executable
            # tick and released at the last: pointer moves in between keep
            # the touch held (Android swipe / virtual-joystick movement).
            path = chunk.pointer_drag
            executable = sum(
                1
                for i in range(chunk.horizon)
                if chunk.effective_from.value_ns + interval_ns * i < chunk.expires_at.value_ns
            )
            position = min(index, executable - 1)
            path_index = min(
                round(position / max(executable - 1, 1) * (len(path) - 1)),
                len(path) - 1,
            )
            px, py = path[path_index]
            pointer_x, pointer_y = float(px), float(py)
            pointer_down = index == 0
            pointer_up = index == executable - 1
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
                pointer_x=pointer_x,
                pointer_y=pointer_y,
                pointer_down=pointer_down,
                pointer_up=pointer_up,
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
        *,
        pre_action_observation_id: str | None = None,
        pre_action_capture_ns: int | None = None,
        execution_guard: Callable[[], bool] | None = None,
    ) -> ActionChunkSubmission:
        if lease.owner != ControlOwner.FAST_POLICY or lease.mode != ControlMode.PLAY_3D:
            raise ContractViolation("Fast Policy chunk requires a PLAY_3D Fast Policy lease")
        canonical = expand_action_chunk(chunk)
        adapted = tuple(
            (canonical_action, self._environment.adapt_action(canonical_action))
            for canonical_action in canonical
        )
        physical = tuple(action for _, actions in adapted for action in actions)
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
                        proposal.proposal_id,
                    ),
                )
            for canonical_action, physical_actions in adapted:
                for physical_action in physical_actions:
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
                            proposal.proposal_id,
                            canonical_action.action_id,
                        ),
                    )
        scheduled = self._scheduler.schedule(
            decision, target, lease,
            pre_action_observation_id=pre_action_observation_id,
            pre_action_capture_ns=pre_action_capture_ns,
            execution_guard=execution_guard,
        )
        return ActionChunkSubmission(canonical, decision, scheduled)
