"""Resource-bounded configuration for motor feature-head training."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from uga.core.artifact_limits import read_text_limited
from uga.core.errors import ContractViolation
from uga.training.contracts import finite_number, integer, strict_json


@dataclass(frozen=True, slots=True)
class NeuralTrainingConfig:
    hidden_dim: int = 64
    epochs: int = 100
    batch_size: int = 64
    learning_rate: float = 0.01
    weight_decay: float = 0.001
    seed: int = 0
    device: str = "cpu"
    patience: int = 20
    max_seconds: float = 300.0
    gradient_clip: float = 1.0

    def __post_init__(self) -> None:
        for name, lo, hi in (
            ("hidden_dim", 1, 256),
            ("epochs", 1, 2000),
            ("batch_size", 1, 1024),
            ("seed", 0, 2**31 - 1),
            ("patience", 1, 2000),
        ):
            integer(getattr(self, name), name, lo, hi)
        for name, lower, upper in (
            ("learning_rate", 1e-6, 1.0),
            ("weight_decay", 0.0, 1.0),
            ("max_seconds", 0.01, 86400.0),
            ("gradient_clip", 0.01, 100.0),
        ):
            value = finite_number(getattr(self, name), name)
            if not lower <= value <= upper:
                raise ContractViolation(f"{name} must be in [{lower}, {upper}]")
        if self.device not in ("cpu", "cuda"):
            raise ContractViolation("device must be cpu or cuda; fallback is never implicit")

    @classmethod
    def load(cls, path: str | Path) -> NeuralTrainingConfig:
        payload = strict_json(read_text_limited(path, 64 * 1024, "neural training config"))
        if not isinstance(payload, dict):
            raise ContractViolation("neural training config must be an object")
        try:
            return cls(**payload)
        except TypeError as exc:
            raise ContractViolation("unknown neural training config fields") from exc
