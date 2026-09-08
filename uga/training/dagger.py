from __future__ import annotations

from dataclasses import dataclass

from uga.core.errors import ContractViolation
from uga.dataset.processor import DatasetSplit
from uga.time.clock import UGATime


@dataclass(frozen=True, slots=True)
class HumanOverride:
    override_id: str
    episode_id: str
    observation_id: str
    split: DatasetSplit
    override_start: UGATime
    override_end: UGATime
    reason: str
    original_agent_action_json: str
    human_action_json: str

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.override_id, self.episode_id, self.observation_id, self.reason)
        ):
            raise ContractViolation("human override requires id and reason")
        if self.override_end < self.override_start:
            raise ContractViolation("human override end cannot precede start")
        if not self.original_agent_action_json or not self.human_action_json:
            raise ContractViolation("human override requires original and corrected actions")


@dataclass(frozen=True, slots=True)
class DaggerIteration:
    iteration: int
    checkpoint_before: str
    checkpoint_after: str
    override_count: int
    failure_count: int


class DaggerPipeline:
    """Collects closed-loop corrections before handing them to retraining."""

    def __init__(self) -> None:
        self._overrides: dict[str, HumanOverride] = {}
        self._failures: list[tuple[str, DatasetSplit]] = []

    def capture_override(self, override: HumanOverride) -> None:
        if override.override_id in self._overrides:
            raise ContractViolation(f"duplicate human override: {override.override_id}")
        if override.split != DatasetSplit.TRAIN:
            raise ContractViolation("DAgger corrections must come from the train split")
        self._overrides[override.override_id] = override

    def capture_failure(self, episode_id: str, split: DatasetSplit) -> None:
        if not episode_id.strip():
            raise ContractViolation("failure episode id cannot be blank")
        if split != DatasetSplit.TRAIN:
            raise ContractViolation("DAgger failures must come from the train split")
        self._failures.append((episode_id, split))

    def correction_set(self) -> tuple[HumanOverride, ...]:
        return tuple(self._overrides.values())

    def finish_iteration(
        self, iteration: int, checkpoint_before: str, checkpoint_after: str
    ) -> DaggerIteration:
        if iteration < 1 or not checkpoint_before.strip() or not checkpoint_after.strip():
            raise ContractViolation("invalid DAgger iteration metadata")
        return DaggerIteration(
            iteration,
            checkpoint_before,
            checkpoint_after,
            len(self._overrides),
            len(self._failures),
        )
