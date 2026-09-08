from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from uga.benchmark.schema import BenchmarkEnvironment, BenchmarkRun, BenchmarkTask
from uga.core.errors import ContractViolation


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


class BenchmarkRunner:
    def run(
        self, tasks: tuple[BenchmarkTask, ...], environments: dict[str, BenchmarkEnvironment]
    ) -> BenchmarkReport:
        if not tasks:
            raise ContractViolation("benchmark requires tasks")
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
                ):
                    raise ContractViolation(
                        "benchmark environment returned the wrong task identity"
                    )
                runs.append(result)
        return self.summarize(tuple(runs))

    def summarize(self, runs: tuple[BenchmarkRun, ...]) -> BenchmarkReport:
        if not runs:
            raise ContractViolation("benchmark report requires runs")
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
        )

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
