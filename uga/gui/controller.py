from __future__ import annotations

from dataclasses import dataclass

from uga.control.arbiter import ActionArbiter, ArbiterDecision
from uga.control.lease import ControlLease
from uga.control.physical import PhysicalAction
from uga.control.scheduler import ActionScheduler
from uga.gui.control_bridge import GuiControlBridge
from uga.gui.schema import GuiAction
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.schema import ActionProvenance
from uga.windows.coordinates import CoordinateTransform
from uga.windows.window_identity import WindowIdentity


@dataclass(frozen=True, slots=True)
class GuiActionSubmission:
    physical_actions: tuple[PhysicalAction, ...]
    decision: ArbiterDecision | None
    scheduled_physical_actions: int


class GuiActionController:
    """Routes one grounded GUI action through bridge, arbiter and scheduler."""

    def __init__(
        self,
        arbiter: ActionArbiter,
        scheduler: ActionScheduler,
        recorder: EpisodeWriter | None = None,
    ) -> None:
        self._bridge = GuiControlBridge()
        self._arbiter = arbiter
        self._scheduler = scheduler
        self._recorder = recorder

    def submit(
        self,
        action: GuiAction,
        transform: CoordinateTransform,
        target: WindowIdentity,
        lease: ControlLease,
        *,
        observation_id: str,
        policy_version: str,
    ) -> GuiActionSubmission:
        physical = self._bridge.translate(action, transform)
        proposal = self._bridge.proposal(
            (action,),
            transform,
            lease,
            producer=f"gui-agent:{policy_version}",
            observation_id=observation_id,
        )
        if proposal is None:
            return GuiActionSubmission((), None, 0)
        decision = self._arbiter.decide(proposal)
        if decision.accepted and self._recorder is not None:
            for physical_action in physical:
                self._recorder.record_action(
                    physical_action,
                    ActionProvenance(
                        physical_action.action_id,
                        "GUI_AGENT",
                        policy_version,
                        None,
                        observation_id,
                        None,
                        None,
                        lease.mode.value,
                        lease.lease_id,
                        action.confidence,
                        False,
                        physical_action.lifetime,
                        proposal.proposal_id,
                    ),
                )
        scheduled = self._scheduler.schedule(decision, target, lease)
        return GuiActionSubmission(physical, decision, scheduled)
