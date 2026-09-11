from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass

from uga.benchmark.schema import BenchmarkEnvironment, BenchmarkRun, BenchmarkTask
from uga.core.artifact_limits import DEFAULT_ARTIFACT_LIMITS, ArtifactResourceLimits
from uga.core.errors import ContractViolation

_SOURCE_REVISION = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True, slots=True)
class LatencyPercentiles:
    p50: float
    p95: float
    p99: float


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    runs: int
    games: tuple[str, ...]
    game_success_rates: tuple[tuple[str, float], ...]
    split_success_rates: tuple[tuple[str, float], ...]
    success_rate: float
    mean_completion_seconds: float
    mean_human_baseline_seconds: float | None
    agent_human_time_ratio: float | None
    human_intervention_rate: float
    recovery_rate: float
    stuck_rate: float
    wrong_mode_rate: float
    lease_conflict_rate: float
    expired_action_rate: float
    camera_smoothness: float
    capture_latency: LatencyPercentiles
    policy_latency: LatencyPercentiles
    action_age: LatencyPercentiles
    end_to_end_latency: LatencyPercentiles
    task_plan_sha256: str
    policy_artifact_sha256: str | None
    source_revision: str = "workspace-unversioned"

    def __post_init__(self) -> None:
        if (
            self.source_revision != "workspace-unversioned"
            and _SOURCE_REVISION.fullmatch(self.source_revision) is None
        ):
            raise ContractViolation("benchmark report source revision is invalid")


class BenchmarkRunner:
    def __init__(
        self,
        *,
        limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
        source_revision: str = "workspace-unversioned",
    ) -> None:
        self._limits = limits
        self._source_revision = source_revision

    def run(
        self, tasks: tuple[BenchmarkTask, ...], environments: dict[str, BenchmarkEnvironment]
    ) -> BenchmarkReport:
        if not tasks:
            raise ContractViolation("benchmark requires tasks")
        if len(tasks) > self._limits.max_benchmark_tasks:
            raise ContractViolation("benchmark exceeds the task resource limit")
        if sum(task.repeat for task in tasks) > self._limits.max_benchmark_runs:
            raise ContractViolation("benchmark plan exceeds the run resource limit")
        runs: list[BenchmarkRun] = []
        for task in tasks:
            try:
                environment = environments[task.game_id]
            except KeyError as exc:
                raise ContractViolation(f"missing benchmark environment: {task.game_id}") from exc
            for repetition in range(task.repeat):
                result = environment.run(task, repetition)
                if (
                    result.task_id != task.task_id
                    or result.game_id != task.game_id
                    or result.split != task.split
                    or result.repetition != repetition
                ):
                    raise ContractViolation(
                        "benchmark environment returned the wrong task identity"
                    )
                runs.append(result)
        return self.summarize(tuple(runs), tasks)

    def summarize(
        self,
        runs: tuple[BenchmarkRun, ...],
        tasks: tuple[BenchmarkTask, ...],
        *,
        expected_policy_artifact_sha256: str | None = None,
    ) -> BenchmarkReport:
        if not runs:
            raise ContractViolation("benchmark report requires runs")
        if len(tasks) > self._limits.max_benchmark_tasks:
            raise ContractViolation("benchmark report exceeds the task resource limit")
        if len(runs) > self._limits.max_benchmark_runs:
            raise ContractViolation("benchmark report exceeds the run resource limit")
        if any(
            len(values) > self._limits.max_latency_samples_per_group
            for run in runs
            for values in (
                run.capture_latency_ms,
                run.policy_latency_ms,
                run.action_age_ms,
                run.end_to_end_latency_ms,
            )
        ):
            raise ContractViolation("benchmark run exceeds the latency-sample resource limit")
        latency_count = sum(
            len(values)
            for run in runs
            for values in (
                run.capture_latency_ms,
                run.policy_latency_ms,
                run.action_age_ms,
                run.end_to_end_latency_ms,
            )
        )
        if latency_count > self._limits.max_latency_samples_total:
            raise ContractViolation("benchmark report exceeds the latency resource limit")
        self._verify_cohort(runs, tasks)
        policy_digests = {run.policy_artifact_sha256 for run in runs}
        if len(policy_digests) != 1:
            raise ContractViolation("benchmark runs mix policy artifact identities")
        policy_artifact_sha256 = next(iter(policy_digests))
        if (
            expected_policy_artifact_sha256 is not None
            and policy_artifact_sha256 != expected_policy_artifact_sha256
        ):
            raise ContractViolation("benchmark policy artifact identity does not match")
        count = len(runs)
        actions = sum(run.total_actions for run in runs)
        recoveries = sum(run.recovery_attempts for run in runs)
        human_baselines = tuple(
            value for run in runs if (value := run.human_baseline_seconds) is not None
        )
        mean_human = sum(human_baselines) / len(human_baselines) if human_baselines else None
        mean_agent = sum(run.completion_seconds for run in runs) / count
        if actions < 1:
            raise ContractViolation("benchmark runs contain no actions")
        return BenchmarkReport(
            count,
            tuple(sorted({run.game_id for run in runs})),
            self._success_rates(runs, key="game"),
            self._success_rates(runs, key="split"),
            sum(run.success for run in runs) / count,
            mean_agent,
            mean_human,
            mean_agent / mean_human if mean_human is not None else None,
            sum(run.human_interventions > 0 for run in runs) / count,
            (sum(run.recovery_successes for run in runs) / recoveries if recoveries else 0.0),
            sum(run.stuck for run in runs) / count,
            sum(run.wrong_mode_count for run in runs) / actions,
            sum(run.lease_conflicts for run in runs) / actions,
            sum(run.expired_actions for run in runs) / actions,
            1.0 / (1.0 + sum(run.camera_jerk for run in runs) / count),
            self._latency(value for run in runs for value in run.capture_latency_ms),
            self._latency(value for run in runs for value in run.policy_latency_ms),
            self._latency(value for run in runs for value in run.action_age_ms),
            self._latency(value for run in runs for value in run.end_to_end_latency_ms),
            self._task_plan_digest(tasks),
            policy_artifact_sha256,
            self._source_revision,
        )

    @staticmethod
    def _verify_cohort(runs: tuple[BenchmarkRun, ...], tasks: tuple[BenchmarkTask, ...]) -> None:
        if not tasks:
            raise ContractViolation("benchmark summary requires an expected task plan")
        expected = [
            (task.task_id, task.game_id, repetition, task.split)
            for task in tasks
            for repetition in range(task.repeat)
        ]
        actual = [(run.task_id, run.game_id, run.repetition, run.split) for run in runs]
        if len(set(expected)) != len(expected):
            raise ContractViolation("benchmark task plan contains duplicate run identities")
        if len(set(actual)) != len(actual):
            raise ContractViolation("benchmark results contain duplicate run identities")
        if set(actual) != set(expected):
            raise ContractViolation("benchmark results do not match the expected task cohort")

    @staticmethod
    def _task_plan_digest(tasks: tuple[BenchmarkTask, ...]) -> str:
        payload = [
            {
                "task_id": task.task_id,
                "game_id": task.game_id,
                "instruction": task.instruction,
                "timeout_seconds": task.timeout_seconds,
                "repeat": task.repeat,
                "evaluator": task.evaluator,
                "split": task.split.value,
            }
            for task in tasks
        ]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _success_rates(
        runs: tuple[BenchmarkRun, ...], *, key: str
    ) -> tuple[tuple[str, float], ...]:
        labels = tuple(sorted({run.game_id if key == "game" else run.split.value for run in runs}))
        return tuple(
            (
                label,
                sum(
                    run.success
                    for run in runs
                    if (run.game_id if key == "game" else run.split.value) == label
                )
                / sum(
                    1
                    for run in runs
                    if (run.game_id if key == "game" else run.split.value) == label
                ),
            )
            for label in labels
        )

    @staticmethod
    def _latency(values: Iterable[float]) -> LatencyPercentiles:
        ordered = sorted(float(value) for value in values)
        if not ordered or any(not math.isfinite(value) or value < 0 for value in ordered):
            raise ContractViolation("benchmark latency samples are invalid")

        def percentile(fraction: float) -> float:
            return ordered[math.ceil(fraction * len(ordered)) - 1]

        return LatencyPercentiles(percentile(0.50), percentile(0.95), percentile(0.99))
