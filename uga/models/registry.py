from __future__ import annotations

import importlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from uga.core.errors import BackendUnavailableError, ContractViolation


class ModelRole(StrEnum):
    PLANNER = "planner"
    FAST_POLICY = "fast_policy"
    GUI = "gui"
    MODE_ROUTER = "mode_router"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    role: ModelRole
    provider: str
    model: str | None
    checkpoint: str | None
    enabled: bool

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ContractViolation("model provider cannot be blank")
        if self.enabled and not (self.model or self.checkpoint):
            raise ContractViolation("enabled model requires model or checkpoint")


class ModelRegistry:
    def __init__(self, specs: tuple[ModelSpec, ...]) -> None:
        self._specs = {spec.role: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise ContractViolation("model roles must be unique")

    def get(self, role: ModelRole, *, require_enabled: bool = True) -> ModelSpec:
        try:
            spec = self._specs[role]
        except KeyError as exc:
            raise ContractViolation(f"model role is not configured: {role}") from exc
        if require_enabled and not spec.enabled:
            raise BackendUnavailableError(f"model role is disabled: {role}")
        return spec


def load_model_registry(path: str | Path) -> ModelRegistry:
    try:
        yaml = importlib.import_module("yaml")
    except ImportError as exc:
        raise BackendUnavailableError(
            "model registry requires the declared PyYAML dependency"
        ) from exc
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), dict):
        raise ContractViolation("model registry requires a models object")
    specs: list[ModelSpec] = []
    models: dict[str, Any] = raw["models"]
    for role_name, value in models.items():
        if not isinstance(value, dict):
            raise ContractViolation(f"model role {role_name} must be an object")
        specs.append(
            ModelSpec(
                ModelRole(str(role_name)),
                str(value["provider"]),
                None if value.get("model") is None else str(value["model"]),
                None if value.get("checkpoint") is None else str(value["checkpoint"]),
                bool(value.get("enabled", False)),
            )
        )
    return ModelRegistry(tuple(specs))
