from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    iter_text_lines_limited,
    parse_json_text,
    read_text_limited,
    sha256_file_limited,
)
from uga.core.errors import ContractViolation
from uga.recording.replay import ReplayEngine
from uga.release.revision import validate_source_revision

_REQUIRED_EPISODE_FILES = frozenset(
    {
        "actions.parquet",
        "annotations.jsonl",
        "events.jsonl",
        "metadata.json",
        "metrics.json",
        "observations.parquet",
        "planner.jsonl",
        "provenance.parquet",
        "run.json",
        "tasks.json",
        "timeline.parquet",
        "video.mp4",
    }
)


@dataclass(frozen=True, slots=True)
class LivePlanEntry:
    episode_id: str
    goal_id: str
    repetition: int
    role: str
    reviewed: bool
    wrong_window: bool
    wrong_target: bool
    critical_error: bool
    loop_detection_rounds: int | None


def _object(path: Path, label: str) -> dict[str, Any]:
    value = parse_json_text(
        read_text_limited(path, DEFAULT_ARTIFACT_LIMITS.max_document_bytes, label)
    )
    if not isinstance(value, dict):
        raise ContractViolation(f"{label} must be a JSON object")
    return value


def _strict_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ContractViolation(f"{label} must be a JSON boolean")
    return bool(value)


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractViolation(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ContractViolation(f"{label} must be finite")
    return result


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractViolation(f"{label} must be an integer")
    return value


def _safe_episode(root: Path, episode_id: str) -> Path:
    pure = PurePosixPath(episode_id)
    if pure.is_absolute() or len(pure.parts) != 1 or "\\" in episode_id:
        raise ContractViolation("live qualification episode id must be a safe path component")
    episode = (root / episode_id).resolve()
    if episode.parent != root or not episode.is_dir():
        raise ContractViolation(f"live qualification Episode is missing: {episode_id}")
    return episode


def _load_plan(path: Path, source_revision: str) -> tuple[LivePlanEntry, ...]:
    payload = _object(path, "VLM live qualification plan")
    if (
        payload.get("schema") != "uga.vlm_live_plan"
        or payload.get("schema_version") != "1.1"
        or payload.get("source_revision") != source_revision
    ):
        raise ContractViolation("VLM live qualification plan envelope is invalid")
    rows = payload.get("episodes")
    if not isinstance(rows, list) or not rows:
        raise ContractViolation("VLM live qualification plan requires Episodes")
    entries: list[LivePlanEntry] = []
    identities: set[tuple[str, str, int]] = set()
    episode_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ContractViolation("VLM live plan Episode must be an object")
        episode_id = row.get("episode_id")
        goal_id = row.get("goal_id")
        role = row.get("role")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (episode_id, goal_id, role)
        ):
            raise ContractViolation("VLM live plan Episode identity is invalid")
        assert isinstance(episode_id, str)
        assert isinstance(goal_id, str)
        assert isinstance(role, str)
        if role not in {"task", "loop"}:
            raise ContractViolation("VLM live plan role must be task or loop")
        repetition = _integer(row.get("repetition"), "VLM live repetition")
        if repetition < 0:
            raise ContractViolation("VLM live repetition cannot be negative")
        rounds_raw = row.get("loop_detection_rounds")
        rounds = (
            None
            if rounds_raw is None
            else _integer(rounds_raw, "loop detection rounds")
        )
        if role == "loop" and (rounds is None or rounds < 1):
            raise ContractViolation("loop Episode requires positive detection rounds")
        if role == "task" and rounds is not None:
            raise ContractViolation("ordinary task Episode cannot claim loop detection rounds")
        entry = LivePlanEntry(
            episode_id,
            goal_id,
            repetition,
            role,
            _strict_bool(row.get("reviewed"), "VLM live reviewed"),
            _strict_bool(row.get("wrong_window"), "VLM live wrong_window"),
            _strict_bool(row.get("wrong_target"), "VLM live wrong_target"),
            _strict_bool(row.get("critical_error"), "VLM live critical_error"),
            rounds,
        )
        identity = (role, goal_id, repetition)
        if identity in identities or episode_id in episode_ids:
            raise ContractViolation("VLM live qualification plan contains duplicate identity")
        identities.add(identity)
        episode_ids.add(episode_id)
        entries.append(entry)
    return tuple(entries)


def _verify_checksums(episode: Path) -> None:
    payload = _object(episode / "checksum.json", "Episode checksum manifest")
    if payload.get("algorithm") != "sha256" or not isinstance(payload.get("files"), dict):
        raise ContractViolation("Episode checksum manifest is invalid")
    files = payload["files"]
    assert isinstance(files, dict)
    if not _REQUIRED_EPISODE_FILES.issubset(files):
        raise ContractViolation("VLM qualification Episode is missing required recorded files")
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ContractViolation("Episode checksum entry is invalid")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
            raise ContractViolation("Episode checksum path is unsafe")
        candidate = (episode / relative).resolve()
        if episode not in candidate.parents or not candidate.is_file():
            raise ContractViolation(f"Episode checksum file is missing: {relative}")
        maximum = (
            DEFAULT_ARTIFACT_LIMITS.max_video_bytes
            if candidate.name == "video.mp4"
            else DEFAULT_ARTIFACT_LIMITS.max_parquet_file_bytes
            if candidate.suffix == ".parquet"
            else DEFAULT_ARTIFACT_LIMITS.max_jsonl_bytes
            if candidate.suffix == ".jsonl"
            else DEFAULT_ARTIFACT_LIMITS.max_document_bytes
        )
        observed = sha256_file_limited(candidate, maximum, f"Episode file {relative}")
        if observed != expected:
            raise ContractViolation(f"Episode checksum mismatch: {relative}")


def _planner_summary(path: Path) -> dict[str, object]:
    terminal: dict[str, Any] | None = None
    stale_discards = 0
    stale_executions = 0
    non_action_executions = 0
    for line_number, line in iter_text_lines_limited(
        path,
        maximum_bytes=DEFAULT_ARTIFACT_LIMITS.max_jsonl_bytes,
        maximum_line_bytes=DEFAULT_ARTIFACT_LIMITS.max_jsonl_line_bytes,
        label="Episode planner journal",
    ):
        if not line.strip():
            continue
        value = parse_json_text(line)
        if not isinstance(value, dict) or not isinstance(value.get("payload"), dict):
            raise ContractViolation(f"invalid planner journal line {line_number}")
        payload = value["payload"]
        assert isinstance(payload, dict)
        if payload.get("kind") == "terminal":
            terminal = payload
            continue
        disposition = payload.get("disposition")
        reason = str(payload.get("supervision_reason", "")).casefold()
        stale_reason = any(
            marker in reason
            for marker in ("stale", "generation", "target pixels changed", "before execution")
        )
        if stale_reason:
            stale_discards += disposition == "reobserve"
            stale_executions += disposition in {"execute", "recover"}
        outcome = payload.get("outcome")
        data = outcome.get("data") if isinstance(outcome, dict) else None
        kind = data.get("kind") if isinstance(data, dict) else None
        if kind in {"wait", "done", "abstain"}:
            non_action_executions += disposition in {"execute", "recover"}
    if terminal is None or not isinstance(terminal.get("closed_loop"), dict):
        raise ContractViolation("VLM Episode requires a terminal closed-loop planner record")
    return {
        "terminal": terminal,
        "stale_discards": stale_discards,
        "stale_executions": stale_executions,
        "non_action_executions": non_action_executions,
    }


def build_vlm_live_qualification_report(
    *,
    plan_path: str | Path,
    episodes_root: str | Path,
    output_path: str | Path,
    source_revision: str,
    require_qualification_volume: bool = True,
) -> Path:
    validate_source_revision(source_revision)
    plan = Path(plan_path).resolve()
    episodes = Path(episodes_root).resolve()
    output = Path(output_path).resolve()
    evidence_root = output.parent
    if evidence_root not in plan.parents or evidence_root not in episodes.parents:
        raise ContractViolation("VLM live plan and Episodes must be beneath the report directory")
    entries = _load_plan(plan, source_revision)
    task_counts: Counter[str] = Counter(
        entry.goal_id for entry in entries if entry.role == "task"
    )
    task_total = sum(task_counts.values())
    loop_total = sum(entry.role == "loop" for entry in entries)
    volume_passed = (
        task_total >= 100
        and len(task_counts) >= 20
        and all(count >= 5 for count in task_counts.values())
        and loop_total >= 1
    )
    if require_qualification_volume and not volume_passed:
        raise ContractViolation(
            "VLM live qualification requires 20 goals x 5 task Episodes and loop injection"
        )

    task_successes = 0
    loop_detections = 0
    reviewed = 0
    wrong_window = 0
    wrong_target = 0
    critical_errors = 0
    scheduled = 0
    executed = 0
    stale_discards = 0
    stale_executions = 0
    non_action_executions = 0
    unaccounted_actions = 0
    pending_terminations = 0
    max_same_ineffective = 0
    max_recoveries = 0
    capture_p95 = 0
    capture_max = 0
    artifacts: list[dict[str, object]] = []
    models: set[str] = set()
    for entry in entries:
        episode = _safe_episode(episodes, entry.episode_id)
        _verify_checksums(episode)
        ReplayEngine(episode).validation()
        run = _object(episode / "run.json", "VLM Episode run")
        metrics = _object(episode / "metrics.json", "VLM Episode metrics")
        planner = _planner_summary(episode / "planner.jsonl")
        if (
            run.get("schema") != "uga.run"
            or run.get("schema_version") != "1.1"
            or run.get("run_id") != entry.episode_id
            or run.get("source_revision") != source_revision
            or run.get("source_tree_clean") is not True
            or not isinstance(run.get("model_id"), str)
            or not str(run["model_id"]).strip()
        ):
            raise ContractViolation("VLM Episode source-bound run envelope is invalid")
        models.add(str(run["model_id"]))
        terminal = planner["terminal"]
        assert isinstance(terminal, dict)
        closed = terminal["closed_loop"]
        assert isinstance(closed, dict)
        status = closed.get("status")
        if entry.role == "task":
            task_successes += (
                run.get("result") == "success"
                and run.get("termination_reason") == "goal_confirmed"
                and status == "succeeded"
            )
        else:
            detected = (
                entry.loop_detection_rounds is not None
                and entry.loop_detection_rounds <= 2
                and status == "blocked"
                and closed.get("last_loop_finding") is not None
            )
            loop_detections += detected
        reviewed += entry.reviewed
        wrong_window += entry.wrong_window
        wrong_target += entry.wrong_target
        critical_errors += entry.critical_error
        scheduled += _integer(metrics.get("scheduled_actions"), "scheduled actions")
        executed += _integer(metrics.get("executed_actions"), "executed actions")
        logical = _integer(metrics.get("logical_actions_issued"), "logical actions")
        verified = _integer(metrics.get("verified_effect_actions"), "verified effects")
        ineffective = _integer(metrics.get("ineffective_actions"), "ineffective actions")
        unaccounted_actions += max(0, logical - verified - ineffective)
        pending_terminations += _integer(
            metrics.get("pending_action_at_termination"), "pending action termination"
        )
        max_same_ineffective = max(
            max_same_ineffective,
            _integer(
                metrics.get("max_consecutive_same_ineffective_action"),
                "same ineffective action count",
            ),
        )
        max_recoveries = max(
            max_recoveries,
            _integer(metrics.get("recovery_count"), "recovery count"),
        )
        capture_p95 = max(
            capture_p95,
            _integer(metrics.get("capture_gap_p95_ns"), "capture p95 gap"),
        )
        capture_max = max(
            capture_max,
            _integer(metrics.get("capture_gap_max_ns"), "capture max gap"),
        )
        stale_discards += _integer(planner["stale_discards"], "stale discard count")
        stale_executions += _integer(planner["stale_executions"], "stale execution count")
        non_action_executions += _integer(
            planner["non_action_executions"], "non-action execution count"
        )
        artifacts.append(
            {
                "episode_id": entry.episode_id,
                "run_sha256": sha256_file_limited(
                    episode / "run.json",
                    DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
                    "VLM Episode run",
                ),
                "metrics_sha256": sha256_file_limited(
                    episode / "metrics.json",
                    DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
                    "VLM Episode metrics",
                ),
                "planner_sha256": sha256_file_limited(
                    episode / "planner.jsonl",
                    DEFAULT_ARTIFACT_LIMITS.max_jsonl_bytes,
                    "VLM Episode planner",
                ),
                "checksum_sha256": sha256_file_limited(
                    episode / "checksum.json",
                    DEFAULT_ARTIFACT_LIMITS.max_checksum_bytes,
                    "VLM Episode checksums",
                ),
            }
        )

    success_rate = task_successes / task_total if task_total else 0.0
    loop_rate = loop_detections / loop_total if loop_total else 0.0
    action_ratio = executed / scheduled if scheduled else 0.0
    checks = (
        ("task_success_rate", success_rate >= 0.95, success_rate, ">=", 0.95),
        ("action_execution_ratio", action_ratio >= 0.99, action_ratio, ">=", 0.99),
        ("loop_detection_rate", loop_rate == 1.0, loop_rate, "==", 1.0),
        ("reviewed_episodes", reviewed == len(entries), reviewed, "==", len(entries)),
        ("wrong_window_count", wrong_window == 0, wrong_window, "==", 0),
        ("wrong_target_count", wrong_target == 0, wrong_target, "==", 0),
        ("critical_error_count", critical_errors == 0, critical_errors, "==", 0),
        ("stale_action_count", stale_executions == 0, stale_executions, "==", 0),
        (
            "non_action_physical_count",
            non_action_executions == 0,
            non_action_executions,
            "==",
            0,
        ),
        ("unaccounted_action_count", unaccounted_actions == 0, unaccounted_actions, "==", 0),
        (
            "pending_action_termination_count",
            pending_terminations == 0,
            pending_terminations,
            "==",
            0,
        ),
        (
            "max_consecutive_same_ineffective_action",
            max_same_ineffective <= 2,
            max_same_ineffective,
            "<=",
            2,
        ),
        ("max_recovery_count", max_recoveries <= 2, max_recoveries, "<=", 2),
        ("capture_gap_p95_ns", capture_p95 <= 350_000_000, capture_p95, "<=", 350_000_000),
        ("capture_gap_max_ns", capture_max <= 500_000_000, capture_max, "<=", 500_000_000),
    )
    payload = {
        "schema": "uga.vlm_live_qualification",
        "schema_version": "1.1",
        "source_revision": source_revision,
        "passed": volume_passed and all(item[1] for item in checks),
        "qualification_volume": volume_passed,
        "model_ids": sorted(models),
        "plan": plan.relative_to(evidence_root).as_posix(),
        "plan_sha256": sha256_file_limited(
            plan,
            DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
            "VLM live qualification plan",
        ),
        "episodes_root": episodes.relative_to(evidence_root).as_posix(),
        "task_episodes": task_total,
        "task_goals": len(task_counts),
        "loop_episodes": loop_total,
        "stale_results_discarded": stale_discards,
        "checks": [
            {
                "metric": metric,
                "passed": passed,
                "observed": observed,
                "operator": operator,
                "threshold": threshold,
            }
            for metric, passed, observed, operator, threshold in checks
        ],
        "artifacts": artifacts,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return output
