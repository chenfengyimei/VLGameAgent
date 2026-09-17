from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.policy.neural_motor import NeuralMotorCheckpoint
from uga.training.behavior_cloning import MotorTrainingSample
from uga.training.neural_config import NeuralTrainingConfig
from uga.training.neural_motor import evaluate_head, train_neural_head

torch = pytest.importorskip("torch")


def samples(episode: str) -> tuple[MotorTrainingSample, ...]:
    return tuple(
        MotorTrainingSample(
            (x, y), 0.5 * x, 0.5 * y, 0.0, 0.0, 1 if x * y > 0 else 0, episode, f"o{i}", f"a{i}"
        )
        for i, (x, y) in enumerate([(-1.0, -1.0), (-1.0, 1.0), (1.0, -1.0), (1.0, 1.0)])
    )


def config() -> NeuralTrainingConfig:
    return NeuralTrainingConfig(
        hidden_dim=16, epochs=120, batch_size=4, learning_rate=0.03, patience=120
    )


def train(**kw: object):
    return train_neural_head(
        samples("train"),
        samples("validation"),
        policy_version="test",
        encoder_version="xor-features/v1",
        config=config(),
        **kw,
    )


def test_gradients_learn_nonlinear_axes_and_buttons(tmp_path: Path) -> None:
    model, report = train()
    metrics = evaluate_head(model, samples("heldout"))
    assert metrics.action_accuracy == 1.0 and metrics.button_f1 == 1.0
    assert report["validation"]["loss"] < report["initial_validation"]["loss"] * 0.1
    assert report["device"] == "cpu" and not report["release_qualified"]
    assert report["test_split_used"] is False
    assert model == NeuralMotorCheckpoint.load(model.save(tmp_path / "model.json"))
    logits, _ = model.raw_predict((1.0, -1.0))
    with torch.no_grad():
        hidden = torch.tanh(
            torch.tensor(model.hidden_weight) @ torch.tensor((1.0, -1.0))
            + torch.tensor(model.hidden_bias)
        )
        expected = torch.tensor(model.output_weight) @ hidden + torch.tensor(model.output_bias)
    assert logits == pytest.approx(expected.tolist(), abs=2e-6)


def test_seed_reproducibility_and_rng_restoration() -> None:
    state = torch.random.get_rng_state().clone()
    first, _ = train()
    second, _ = train()
    assert first == second
    assert torch.equal(state, torch.random.get_rng_state())


def test_validation_does_not_change_normalization() -> None:
    val = tuple(replace(s, features=(999.0, 999.0)) for s in samples("validation"))
    model, report = train_neural_head(
        samples("train"),
        val,
        policy_version="test",
        encoder_version="xor",
        config=replace(config(), epochs=1),
    )
    assert model.mean == (0.0, 0.0) and model.upper == (1.0, 1.0)
    assert report["validation"]["abstention_rate"] == 1.0
    assert model.validation_reliability == 0


def test_cancellation() -> None:
    with pytest.raises(ContractViolation, match="cancelled"):
        train(cancelled=lambda: True)


def test_empty_and_leaking_sets_rejected() -> None:
    for val in ((), samples("train")):
        with pytest.raises(ContractViolation):
            train_neural_head(
                samples("train"), val, policy_version="test", encoder_version="xor", config=config()
            )


def test_cuda_never_silently_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(BackendUnavailableError, match="CUDA"):
        train_neural_head(
            samples("train"),
            samples("validation"),
            policy_version="test",
            encoder_version="xor",
            config=replace(config(), device="cuda"),
        )


def test_training_resource_budget() -> None:
    large = tuple(replace(s, features=(0.0,) * 8192) for s in samples("train"))
    with pytest.raises(ContractViolation, match="budget"):
        train_neural_head(
            large,
            (replace(large[0], episode_id="validation"),),
            policy_version="test",
            encoder_version="xor",
            config=replace(config(), hidden_dim=256),
        )


def test_ambient_dtype_isolated() -> None:
    previous = torch.get_default_dtype()
    try:
        torch.set_default_dtype(torch.float64)
        model, _ = train()
        assert evaluate_head(model, samples("heldout")).action_accuracy == 1.0
        assert torch.get_default_dtype() == torch.float64
    finally:
        torch.set_default_dtype(previous)


def test_evaluation_resource_budget(monkeypatch) -> None:
    from tests.unit.test_neural_checkpoint import checkpoint
    from uga.training import neural_motor

    monkeypatch.setattr(neural_motor, "MAX_EVALUATION_WORK", 1)
    with pytest.raises(ContractViolation, match="bounded"):
        evaluate_head(checkpoint(), samples("test"))
