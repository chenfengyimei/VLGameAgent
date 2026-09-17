"""Causal receipt binding for developer-owned scripted fixtures only.

Reset/menu diagnostics are not motor training labels. Logical intents are
recorded before scheduling, but become qualified only with complete successful
child receipts. This also prevents aborted, reset-only episodes from falling
back to physical diagnostic actions as apparently positive examples.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from uga.control.canonical import CanonicalAction
from uga.control.execution_receipt import ExecutionReceipt
from uga.control.lifetime import ActionLifetime
from uga.control.physical import PhysicalAction, RelativeMouseAction
from uga.core.errors import ContractViolation
from uga.environment.fixture_world import FixtureWorld
from uga.recording.episode_writer import EpisodeWriter
from uga.recording.schema import ActionProvenance
from uga.time.clock import UGATime


@dataclass(slots=True)
class _Group:
    action: CanonicalAction
    children: frozenset[str]
    terminal: set[str] = field(default_factory=set)
    pre_observation: str | None = None
    pre_capture_ns: int | None = None


class FixtureEvidenceRecorder:
    def __init__(self, writer: EpisodeWriter) -> None:
        self.writer = writer
        self._by_child: dict[str, _Group] = {}
        self.expected_groups = 0
        self.completed_groups = 0

    def register_cycle(
        self,
        actions: tuple[PhysicalAction, ...],
        cycle: int,
        world: FixtureWorld,
        *,
        lease_id: str,
        proposal_id: str,
        observation_id: str,
    ) -> None:
        prefix = f"fixture-{cycle:04d}"
        move_x, move_y = world.canonical_movement
        selections = (
            (
                "movement",
                tuple(
                    a
                    for a in actions
                    if any(
                        a.action_id in {f"{prefix}-{key}-down", f"{prefix}-{key}-up"}
                        for key in world.movement_keys
                    )
                ),
                {"move_x": move_x, "move_y": move_y},
            ),
            (
                "look",
                tuple(a for a in actions if isinstance(a, RelativeMouseAction)),
                {"look_x": 0.12},
            ),  # fixture: actual dx 12 / profile sensitivity 100
            (
                "interact",
                tuple(
                    a
                    for a in actions
                    if a.action_id in {f"{prefix}-interact-down", f"{prefix}-interact-up"}
                ),
                {"interact": True},
            ),
        )
        for name, children, axes in selections:
            if not children:
                raise ContractViolation(f"fixture cycle has no {name} primitives")
            lifetime = ActionLifetime(
                min(a.lifetime.created_at for a in children),
                min(a.lifetime.effective_from for a in children),
                max(a.lifetime.expires_at for a in children),
            )
            canonical = CanonicalAction(f"{prefix}-{name}-canonical", lifetime, **axes)
            group = _Group(canonical, frozenset(a.action_id for a in children))
            self.writer.record_canonical_action(
                canonical,
                self._provenance(
                    canonical.action_id,
                    lifetime,
                    lease_id,
                    proposal_id,
                    observation_id,
                ),
            )
            for action in children:
                if action.action_id in self._by_child:
                    raise ContractViolation("fixture primitive belongs to two logical groups")
                self._by_child[action.action_id] = group
            self.expected_groups += 1
        for action in actions:
            child_group = self._by_child.get(action.action_id)
            self.writer.record_action(
                action,
                self._provenance(
                    action.action_id,
                    action.lifetime,
                    lease_id,
                    proposal_id,
                    observation_id,
                    None if child_group is None else child_group.action.action_id,
                ),
            )

    def record(
        self, receipts: tuple[ExecutionReceipt, ...], *, observation_id: str, captured_at: UGATime
    ) -> None:
        for receipt in receipts:
            group = self._by_child.get(receipt.action_id)
            pre_id: str | None = observation_id
            pre_ns: int | None = captured_at.value_ns
            if captured_at.value_ns > receipt.at.value_ns:
                pre_id, pre_ns = None, None
            if group is not None:
                if not group.terminal:
                    group.pre_observation, group.pre_capture_ns = pre_id, pre_ns
                pre_id, pre_ns = group.pre_observation, group.pre_capture_ns
                if receipt.action_id in group.terminal:
                    raise ContractViolation("fixture duplicate terminal primitive")
                group.terminal.add(receipt.action_id)
                if group.terminal == group.children:
                    self.completed_groups += 1
            self.writer.record_execution_receipts(
                (
                    replace(
                        receipt,
                        pre_action_observation_id=pre_id,
                        pre_action_capture_ns=pre_ns,
                    ),
                )
            )
            if group is not None and group.terminal == group.children:
                # EpisodeWriter retains provenance; the producer need not keep
                # completed groups in memory throughout an hours-long run.
                for child in group.children:
                    del self._by_child[child]

    @staticmethod
    def _provenance(
        action_id: str,
        lifetime: ActionLifetime,
        lease_id: str,
        proposal_id: str,
        observation_id: str,
        parent: str | None = None,
    ) -> ActionProvenance:
        return ActionProvenance(
            action_id,
            "fixture-qualification",
            "fixture-script-v2",
            None,
            observation_id,
            "fixture-navigation",
            "fixture-complete-task",
            "PLAY_3D",
            lease_id,
            1.0,
            False,
            lifetime,
            proposal_id,
            parent,
        )
