from __future__ import annotations

import uuid
from dataclasses import dataclass
from threading import Lock

from uga.agent.belief import BeliefState
from uga.capture.frame import Frame
from uga.control.lease import ControlLease, ControlMode
from uga.core.errors import ContractViolation
from uga.observation.schema import LatencyContext, Observation
from uga.time.clock import ClockBackend


@dataclass(frozen=True, slots=True)
class ObservationInputs:
    goal: str
    subgoal: str | None = None
    visible_text: tuple[str, ...] = ()
    recent_action_ids: tuple[str, ...] = ()
    recent_events: tuple[str, ...] = ()
    active_skill: str | None = None
    last_success: str | None = None
    last_failure: str | None = None

    def validate(self) -> None:
        if not self.goal.strip():
            raise ContractViolation("observation goal cannot be blank")
        for optional in (self.subgoal, self.active_skill):
            if optional is not None and not optional.strip():
                raise ContractViolation("optional observation text cannot be blank")
        if any(not value.strip() for value in (*self.visible_text, *self.recent_events)):
            raise ContractViolation("observation text and event names cannot be blank")


class ObservationBuilder:
    """Builds replayable observations from capture history on the UGA clock."""

    def __init__(
        self,
        clock: ClockBackend,
        game_id: str,
        inputs: ObservationInputs,
        *,
        inference_budget_ns: int = 200_000_000,
    ) -> None:
        if not game_id.strip() or inference_budget_ns < 0:
            raise ContractViolation("observation builder configuration is invalid")
        inputs.validate()
        self._clock = clock
        self._game_id = game_id
        self._inputs = inputs
        self._inference_budget_ns = inference_budget_ns
        self._lock = Lock()

    def update_inputs(self, inputs: ObservationInputs) -> None:
        inputs.validate()
        with self._lock:
            self._inputs = inputs

    def build(
        self,
        latest: Frame,
        history: tuple[Frame, ...],
        mode: ControlMode,
        lease: ControlLease | None,
    ) -> Observation:
        started = self._clock.now()
        if started < latest.capture_timestamp:
            raise ContractViolation("observation clock predates the captured frame")
        bounded_history = history[-8:]
        if not bounded_history or bounded_history[-1] != latest:
            raise ContractViolation("capture history must end with the latest frame")
        with self._lock:
            inputs = self._inputs
        belief = BeliefState(
            self._game_id,
            mode,
            inputs.goal,
            inputs.subgoal,
            (),
            (),
            (),
            (),
            (),
            (),
            (),
            inputs.last_success,
            inputs.last_failure,
            0.5,
            started,
        )
        ended = self._clock.now()
        return Observation(
            uuid.uuid4().hex,
            ended,
            latest,
            bounded_history,
            mode,
            inputs.goal,
            inputs.subgoal,
            inputs.recent_action_ids,
            inputs.recent_events,
            belief,
            inputs.visible_text,
            inputs.active_skill,
            lease,
            LatencyContext(
                ended.value_ns - latest.capture_timestamp.value_ns,
                ended.value_ns - started.value_ns,
                self._inference_budget_ns,
            ),
        )
