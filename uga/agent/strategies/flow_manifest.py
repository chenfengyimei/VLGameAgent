"""Machine-readable contracts for user-recorded deterministic game flows."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uga.core.artifact_limits import DEFAULT_ARTIFACT_LIMITS, read_text_limited
from uga.core.errors import BackendUnavailableError, ContractViolation


@dataclass(frozen=True, slots=True)
class RecordedFlowStep:
    """One screenshot-backed screen recognition and its deterministic action."""

    step_id: str
    order: int
    evidence: str
    source: str
    recognize: dict[str, Any]
    action: str
    expected: str


@dataclass(frozen=True, slots=True)
class RecordedFlow:
    """A reviewable sequence supplied by the owner of the game session."""

    version: int
    game_id: str
    name: str
    steps: tuple[RecordedFlowStep, ...]


def load_recorded_flow(path: str | Path) -> RecordedFlow:
    """Load and strictly validate a trusted local recorded-flow manifest."""
    try:
        yaml = importlib.import_module("yaml")
    except ImportError as exc:
        raise BackendUnavailableError(
            "recorded-flow YAML requires the declared PyYAML dependency"
        ) from exc
    raw = yaml.safe_load(
        read_text_limited(path, DEFAULT_ARTIFACT_LIMITS.max_config_bytes, "recorded flow")
    )
    if not isinstance(raw, dict):
        raise ContractViolation("recorded-flow root must be an object")
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ContractViolation("recorded-flow steps must be a nonempty list")
    steps: list[RecordedFlowStep] = []
    for index, value in enumerate(steps_raw):
        if not isinstance(value, dict):
            raise ContractViolation(f"recorded-flow step {index} must be an object")
        recognize = value.get("recognize")
        if not isinstance(recognize, dict) or not recognize:
            raise ContractViolation(f"recorded-flow step {index} requires recognition anchors")
        steps.append(
            RecordedFlowStep(
                step_id=_required_text(value, "id", index),
                order=_required_positive_int(value, "order", index),
                evidence=_required_text(value, "evidence", index),
                source=_required_text(value, "source", index),
                recognize=recognize,
                action=_required_text(value, "action", index),
                expected=_required_text(value, "expected", index),
            )
        )
    if len({step.step_id for step in steps}) != len(steps):
        raise ContractViolation("recorded-flow step ids must be unique")
    orders = [step.order for step in steps]
    if orders != sorted(orders) or len(set(orders)) != len(orders):
        raise ContractViolation("recorded-flow step order must be unique and ascending")
    return RecordedFlow(
        version=_required_positive_int(raw, "version", -1),
        game_id=_required_text(raw, "game_id", -1),
        name=_required_text(raw, "name", -1),
        steps=tuple(steps),
    )


def _required_text(value: dict[str, Any], key: str, index: int) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ContractViolation(f"recorded-flow step {index} requires nonempty {key}")
    return result.strip()


def _required_positive_int(value: dict[str, Any], key: str, index: int) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool) or result <= 0:
        raise ContractViolation(f"recorded-flow step {index} requires positive integer {key}")
    return result
