from __future__ import annotations

from typing import Protocol, runtime_checkable

from uga.core.errors import ContractViolation
from uga.gui.schema import GuiResult, GuiTask
from uga.observation.schema import Observation


@runtime_checkable
class GuiAgent(Protocol):
    @property
    def model_version(self) -> str: ...

    def act(self, task: GuiTask, observation: Observation) -> GuiResult: ...


@runtime_checkable
class GuiModel(Protocol):
    @property
    def model_version(self) -> str: ...

    def generate_actions(self, task: GuiTask, observation: Observation) -> GuiResult: ...


class UiTarsBackend:
    """Provider adapter for configurable open UI-TARS-compatible checkpoints."""

    def __init__(self, model: GuiModel) -> None:
        self._model = model

    @property
    def model_version(self) -> str:
        return self._model.model_version

    def act(self, task: GuiTask, observation: Observation) -> GuiResult:
        task.validate()
        observation.validate()
        result = self._model.generate_actions(task, observation)
        if result.task_id != task.task_id or len(result.actions) > task.max_actions:
            raise ContractViolation("GUI provider returned actions outside the task contract")
        for action in result.actions:
            action.validate()
        return result
