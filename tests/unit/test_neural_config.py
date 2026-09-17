from __future__ import annotations

import json
from pathlib import Path

import pytest

from uga.core.errors import ContractViolation
from uga.training.neural_config import NeuralTrainingConfig


@pytest.mark.parametrize(
    "changes",
    [
        {"epochs": True},
        {"epochs": 0},
        {"epochs": 2001},
        {"seed": -1},
        {"learning_rate": float("nan")},
        {"weight_decay": -1},
        {"hidden_dim": 257},
        {"learning_rate": "0.01"},
        {"max_seconds": float("inf")},
        {"device": "auto"},
        {"device": "cuda:99"},
        {"batch_size": 1.5},
        {"gradient_clip": 0},
    ],
)
def test_invalid_configuration(changes: dict[str, object]) -> None:
    with pytest.raises(ContractViolation):
        NeuralTrainingConfig(**changes)


def test_unknown_fields_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"optimizer": "unknown"}))
    with pytest.raises(ContractViolation, match="unknown"):
        NeuralTrainingConfig.load(path)


def test_default_is_explicit_cpu(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    assert NeuralTrainingConfig.load(path).device == "cpu"
