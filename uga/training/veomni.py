from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from uga.core.errors import ContractViolation


@dataclass(frozen=True, slots=True)
class DistributedTrainingJob:
    job_id: str
    model: str
    dataset_path: str
    output_path: str
    stage: str
    nodes: int
    devices_per_node: int
    config: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.job_id, self.model, self.dataset_path, self.output_path, self.stage)
        ):
            raise ContractViolation("distributed job fields cannot be blank")
        if self.nodes < 1 or self.devices_per_node < 1:
            raise ContractViolation("distributed job resources must be positive")


@runtime_checkable
class VeOmniLauncher(Protocol):
    def launch(self, job: DistributedTrainingJob) -> str: ...


class VeOmniBackend:
    """Optional distributed backend; trainer contracts do not depend on its internals."""

    def __init__(self, launcher: VeOmniLauncher) -> None:
        self._launcher = launcher

    def submit(self, job: DistributedTrainingJob) -> str:
        run_id = self._launcher.launch(job)
        if not run_id.strip():
            raise ContractViolation("VeOmni launcher returned an empty run id")
        return run_id
