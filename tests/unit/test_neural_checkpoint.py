from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from uga.core.errors import ContractViolation
from uga.policy.fast_policy import ActionDecoder, PolicyContext
from uga.policy.neural_motor import OUTPUT_DIM, NeuralActionDecoder, NeuralMotorCheckpoint
from uga.time.clock import UGATime


def checkpoint() -> NeuralMotorCheckpoint:
    return NeuralMotorCheckpoint(
        "neural-test",
        "test-features/v1",
        2,
        2,
        (0.0, 0.0),
        (1.0, 1.0),
        (-1.0, -1.0),
        (1.0, 1.0),
        ((1.0, 0.0), (0.0, 1.0)),
        (0.0, 0.0),
        ((1.0, -1.0),) * OUTPUT_DIM,
        (0.0,) * OUTPUT_DIM,
        0.8,
    )


def test_roundtrip(tmp_path: Path) -> None:
    model = checkpoint()
    assert NeuralMotorCheckpoint.load(model.save(tmp_path / "model.json")) == model
    assert model.predict((1.0, -1.0)).axes[0] > 0
    assert model.predict((1.0, -1.0)).buttons == 511
    assert model.predict((-1.0, 1.0)).buttons == 0


def test_outside_support_abstains() -> None:
    pred = checkpoint().predict((100.0, 0.0))
    assert pred.out_of_distribution and pred.confidence == 0
    assert pred.axes == (0.0,) * 4 and pred.buttons == 0


@pytest.mark.parametrize("value", [True, "1", float("nan"), float("inf"), 1e200])
def test_invalid_features(value: object) -> None:
    with pytest.raises(ContractViolation):
        checkpoint().predict((value, 0.0))


@pytest.mark.parametrize(
    "changes",
    [
        dict(input_dim=True),
        dict(hidden_dim=1000),
        dict(scale=(0, 1)),
        dict(output_bias=(0,)),
        dict(lower=(1, 0)),
        dict(hidden_weight=((True, 1), (0, 0))),
        dict(validation_reliability="0.8"),
    ],
)
def test_invalid_checkpoint(changes: dict[str, object]) -> None:
    with pytest.raises(ContractViolation):
        replace(checkpoint(), **changes)


def test_extra_fields_and_duplicates_rejected(tmp_path: Path) -> None:
    path = checkpoint().save(tmp_path / "model.json")
    data = json.loads(path.read_text())
    data["data"]["executable"] = "anything"
    path.write_text(json.dumps(data))
    with pytest.raises(ContractViolation):
        NeuralMotorCheckpoint.load(path)
    path.write_text('{"schema":1,"schema":2}')
    with pytest.raises(ContractViolation, match="duplicate"):
        NeuralMotorCheckpoint.load(path)


def test_existing_action_contract() -> None:
    decoder = NeuralActionDecoder(checkpoint(), horizon=2)
    assert isinstance(decoder, ActionDecoder)
    context = PolicyContext("obs", UGATime(100), (), None)
    chunk = decoder.decode((1.0, -1.0), context)
    assert chunk.horizon == 2 and chunk.observation_id == "obs"
    assert chunk.expires_at.value_ns == 100 + round(2e9 / 30)
    with pytest.raises(ContractViolation):
        decoder.decode((0.0, 0.0), context, horizon=True)
