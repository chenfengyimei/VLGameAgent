from __future__ import annotations

import importlib
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from uga.core.errors import BackendUnavailableError, ContractViolation

_SHA256 = re.compile(r"[0-9a-f]{64}")


class BenchmarkSplit(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


@dataclass(frozen=True, slots=True)
class BenchmarkTask:
    task_id: str
    game_id: str
    instruction: str
    timeout_seconds: int
    repeat: int
    evaluator: str
    split: BenchmarkSplit = BenchmarkSplit.TEST

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.task_id, self.game_id, self.instruction, self.evaluator)
        ):
            raise ContractViolation("benchmark task has blank fields")
        if self.timeout_seconds < 1 or self.repeat < 1:
            raise ContractViolation("benchmark timeout and repeat must be positive")


@dataclass(frozen=True, slots=True)
class BenchmarkRun:
    task_id: str
    game_id: str
    repetition: int
    success: bool
    completion_seconds: float
    human_interventions: int
    recovery_attempts: int
    recovery_successes: int
    stuck: bool
    wrong_mode_count: int
    lease_conflicts: int
    expired_actions: int
    total_actions: int
    camera_jerk: float
    capture_latency_ms: tuple[float, ...]
    policy_latency_ms: tuple[float, ...]
    action_age_ms: tuple[float, ...]
    end_to_end_latency_ms: tuple[float, ...]
    human_baseline_seconds: float | None = None
    split: BenchmarkSplit = BenchmarkSplit.TEST
    policy_artifact_sha256: str | None = None

    def __post_init__(self) -> None:
        if type(self.success) is not bool or type(self.stuck) is not bool:
            raise ContractViolation("benchmark run success and stuck must be booleans")
        if not self.task_id.strip() or not self.game_id.strip() or self.repetition < 0:
            raise ContractViolation("benchmark run identity is invalid")
        counts = (
            self.human_interventions,
            self.recovery_attempts,
            self.recovery_successes,
            self.wrong_mode_count,
            self.lease_conflicts,
            self.expired_actions,
            self.total_actions,
        )
        if (
            self.completion_seconds < 0
            or not math.isfinite(self.completion_seconds)
            or any(value < 0 for value in counts)
            or self.recovery_successes > self.recovery_attempts
            or self.total_actions < 1
            or self.camera_jerk < 0
            or not math.isfinite(self.camera_jerk)
            or (
                self.human_baseline_seconds is not None
                and (
                    self.human_baseline_seconds <= 0
                    or not math.isfinite(self.human_baseline_seconds)
                )
            )
        ):
            raise ContractViolation("benchmark run metrics are invalid")
        latency_groups = (
            self.capture_latency_ms,
            self.policy_latency_ms,
            self.action_age_ms,
            self.end_to_end_latency_ms,
        )
        if any(
            not values or any(value < 0 or not math.isfinite(value) for value in values)
            for values in latency_groups
        ):
            raise ContractViolation("benchmark run latency samples are invalid")
        if (
            self.policy_artifact_sha256 is not None
            and _SHA256.fullmatch(self.policy_artifact_sha256) is None
        ):
            raise ContractViolation("benchmark policy artifact digest must be SHA-256")


@runtime_checkable
class BenchmarkEnvironment(Protocol):
    def run(self, task: BenchmarkTask, repetition: int) -> BenchmarkRun: ...


def load_benchmark_tasks(path: str | Path) -> tuple[BenchmarkTask, ...]:
    try:
        yaml = importlib.import_module("yaml")
    except ImportError as exc:
        raise BackendUnavailableError("benchmark YAML requires PyYAML") from exc
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    values = raw.get("tasks") if isinstance(raw, dict) else None
    if not isinstance(values, list):
        raise ContractViolation("benchmark file requires a tasks list")
    tasks: list[BenchmarkTask] = []
    for value in values:
        if not isinstance(value, dict):
            raise ContractViolation("benchmark task must be an object")
        success = value.get("success", {})
        if not isinstance(success, dict):
            raise ContractViolation("benchmark success must be an object")
        tasks.append(
            BenchmarkTask(
                str(value["id"]),
                str(value["game"]),
                str(value["instruction"]),
                int(value["timeout_seconds"]),
                int(value.get("repeat", 1)),
                str(success["evaluator"]),
                BenchmarkSplit(str(value.get("split", "test"))),
            )
        )
    return tuple(tasks)
