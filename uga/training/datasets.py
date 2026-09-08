from __future__ import annotations

from dataclasses import dataclass

from uga.core.errors import ContractViolation
from uga.dataset.processor import DatasetSplit


@dataclass(frozen=True, slots=True)
class InstructionSample:
    episode_id: str
    split: DatasetSplit
    observation_ids: tuple[str, ...]
    instruction: str
    subgoal: str
    action_chunk_id: str

    def __post_init__(self) -> None:
        if not self.observation_ids or any(
            not value.strip()
            for value in (
                self.episode_id,
                self.instruction,
                self.subgoal,
                self.action_chunk_id,
                *self.observation_ids,
            )
        ):
            raise ContractViolation("instruction sample is incomplete")


@dataclass(frozen=True, slots=True)
class RecoverySample:
    episode_id: str
    split: DatasetSplit
    observation_ids: tuple[str, ...]
    failure_kind: str
    perturbation: str
    correction_action_chunk_id: str
    human_corrected: bool

    def __post_init__(self) -> None:
        if not self.observation_ids or any(
            not value.strip()
            for value in (
                self.failure_kind,
                self.perturbation,
                self.correction_action_chunk_id,
                self.episode_id,
                *self.observation_ids,
            )
        ):
            raise ContractViolation("recovery sample is incomplete")


class RecoveryDataset:
    def __init__(self) -> None:
        self._samples: list[RecoverySample] = []

    def add(self, sample: RecoverySample) -> None:
        if sample.split != DatasetSplit.TRAIN:
            raise ContractViolation("recovery training samples must come from the train split")
        self._samples.append(sample)

    def samples(self) -> tuple[RecoverySample, ...]:
        return tuple(self._samples)

    def human_correction_ratio(self) -> float:
        if not self._samples:
            return 0.0
        return sum(sample.human_corrected for sample in self._samples) / len(self._samples)
