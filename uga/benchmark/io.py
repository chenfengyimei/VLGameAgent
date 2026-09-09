from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from uga.benchmark.runner import BenchmarkReport
from uga.benchmark.schema import BenchmarkRun, BenchmarkSplit
from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    ArtifactResourceLimits,
    iter_text_lines_limited,
)
from uga.core.errors import ContractViolation


def load_benchmark_runs(
    path: str | Path,
    *,
    limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
) -> tuple[BenchmarkRun, ...]:
    runs: list[BenchmarkRun] = []
    latency_samples = 0
    for line_number, line in iter_text_lines_limited(
        path,
        maximum_bytes=limits.max_benchmark_jsonl_bytes,
        maximum_line_bytes=limits.max_benchmark_line_bytes,
        label="benchmark JSONL",
    ):
        if not line.strip():
            continue
        try:
            payload: Any = json.loads(line)
            if not isinstance(payload, dict):
                raise TypeError("expected object")
            latency_names = (
                "capture_latency_ms",
                "policy_latency_ms",
                "action_age_ms",
                "end_to_end_latency_ms",
            )
            if any(
                not isinstance(payload.get(name), list)
                or len(payload[name]) > limits.max_latency_samples_per_group
                for name in latency_names
            ):
                raise ContractViolation("benchmark latency samples exceed the resource limit")
            latency_samples += sum(len(payload[name]) for name in latency_names)
            if latency_samples > limits.max_latency_samples_total:
                raise ContractViolation("benchmark JSONL exceeds the total latency resource limit")
            if len(runs) >= limits.max_benchmark_runs:
                raise ContractViolation("benchmark JSONL exceeds the run resource limit")
            success = _strict_bool(payload["success"], "success")
            stuck = _strict_bool(payload["stuck"], "stuck")
            runs.append(
                BenchmarkRun(
                    str(payload["task_id"]),
                    str(payload["game_id"]),
                    int(payload["repetition"]),
                    success,
                    float(payload["completion_seconds"]),
                    int(payload["human_interventions"]),
                    int(payload["recovery_attempts"]),
                    int(payload["recovery_successes"]),
                    stuck,
                    int(payload["wrong_mode_count"]),
                    int(payload["lease_conflicts"]),
                    int(payload["expired_actions"]),
                    int(payload["total_actions"]),
                    float(payload["camera_jerk"]),
                    tuple(float(value) for value in payload["capture_latency_ms"]),
                    tuple(float(value) for value in payload["policy_latency_ms"]),
                    tuple(float(value) for value in payload["action_age_ms"]),
                    tuple(float(value) for value in payload["end_to_end_latency_ms"]),
                    (
                        None
                        if payload.get("human_baseline_seconds") is None
                        else float(payload["human_baseline_seconds"])
                    ),
                    BenchmarkSplit(str(payload.get("split", "test"))),
                    (
                        None
                        if payload.get("policy_artifact_sha256") is None
                        else str(payload["policy_artifact_sha256"])
                    ),
                )
            )
        except (KeyError, TypeError, ValueError, ContractViolation) as exc:
            raise ContractViolation(f"invalid benchmark JSONL line {line_number}: {exc}") from exc
    if not runs:
        raise ContractViolation("benchmark JSONL contains no runs")
    return tuple(runs)


def _strict_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise ContractViolation(f"benchmark {field} must be a JSON boolean")
    return value


def write_benchmark_report(report: BenchmarkReport, path: str | Path) -> Path:
    destination = Path(path)
    destination.write_text(json.dumps(asdict(report), indent=2) + "\n", encoding="utf-8")
    return destination


def write_benchmark_runs(runs: tuple[BenchmarkRun, ...], path: str | Path) -> Path:
    if not runs:
        raise ContractViolation("benchmark output requires runs")
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(json.dumps(asdict(run), sort_keys=True) + "\n" for run in runs),
        encoding="utf-8",
    )
    return destination
