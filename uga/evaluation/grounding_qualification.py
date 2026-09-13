from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    iter_text_lines_limited,
    parse_json_text,
    sha256_file_limited,
)
from uga.core.errors import ContractViolation
from uga.evaluation.grounding import (
    GroundingEvaluator,
    GroundingMetrics,
    GroundingPrediction,
    GroundingSample,
)
from uga.gui.schema import GuiActionKind
from uga.perception.schema import DecisionKind, GoalStatus, NormalizedBox
from uga.release.revision import validate_source_revision

GROUNDING_CATEGORY_MINIMUMS = {
    "ordinary": 80,
    "terminal": 40,
    "difficult": 40,
    "loop": 40,
}
GROUNDING_THRESHOLDS = {
    "schema_valid_rate": (">=", 1.0),
    "text_f1": (">=", 0.95),
    "decision_kind_accuracy": (">=", 0.95),
    "action_kind_accuracy": (">=", 0.95),
    "box_hit_rate": (">=", 0.95),
    "median_center_error": ("<=", 0.025),
    "p95_center_error": ("<=", 0.05),
    "false_act_rate": ("<=", 0.01),
    "forbidden_action_count": ("<=", 0.0),
    "wrong_window_count": ("<=", 0.0),
}
_QUALIFIED_ACTION_KINDS = frozenset(
    {GuiActionKind.CLICK, GuiActionKind.KEY, GuiActionKind.HOTKEY}
)


@dataclass(frozen=True, slots=True)
class GroundingCorpus:
    samples: tuple[GroundingSample, ...]
    sample_ids: tuple[str, ...]
    categories: tuple[tuple[str, int], ...]
    frame_set_sha256: str


def _box(value: object, label: str) -> NormalizedBox:
    if not isinstance(value, list) or len(value) != 4:
        raise ContractViolation(f"{label} must contain four normalized coordinates")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ContractViolation(f"{label} coordinates must be numbers")
    return NormalizedBox(*(float(item) for item in value))


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ContractViolation(f"{label} must be a list of non-blank strings")
    return tuple(value)


def _rows(path: Path, label: str) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for line_number, line in iter_text_lines_limited(
        path,
        maximum_bytes=DEFAULT_ARTIFACT_LIMITS.max_benchmark_jsonl_bytes,
        maximum_line_bytes=DEFAULT_ARTIFACT_LIMITS.max_benchmark_line_bytes,
        label=label,
    ):
        if not line.strip():
            continue
        try:
            value = parse_json_text(line)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ContractViolation(f"invalid {label} line {line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ContractViolation(f"invalid {label} line {line_number}: expected object")
        rows.append(value)
        if len(rows) > DEFAULT_ARTIFACT_LIMITS.max_benchmark_runs:
            raise ContractViolation(f"{label} exceeds the row resource limit")
    if not rows:
        raise ContractViolation(f"{label} contains no records")
    return tuple(rows)


def _frame_digest(root: Path, frames: object) -> tuple[str, ...]:
    if not isinstance(frames, list) or not 1 <= len(frames) <= 3:
        raise ContractViolation("grounding sample requires one to three frame references")
    digests: list[str] = []
    for frame in frames:
        if not isinstance(frame, dict):
            raise ContractViolation("grounding frame reference must be an object")
        relative = frame.get("path")
        expected = frame.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ContractViolation("grounding frame reference requires path and SHA-256")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
            raise ContractViolation("grounding frame path must be safe and relative")
        candidate = (root / relative).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise ContractViolation(f"grounding frame is missing: {relative}")
        observed = sha256_file_limited(
            candidate,
            DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
            f"grounding frame {relative}",
        )
        if observed != expected:
            raise ContractViolation(f"grounding frame digest mismatch: {relative}")
        digests.append(f"{relative}:{observed}")
    return tuple(digests)


def load_grounding_corpus(
    path: str | Path, *, require_qualification_volume: bool = True
) -> GroundingCorpus:
    source = Path(path).resolve()
    samples: list[GroundingSample] = []
    ids: set[str] = set()
    categories: Counter[str] = Counter()
    frame_records: set[str] = set()
    for row in _rows(source, "grounding annotations JSONL"):
        sample_id = row.get("sample_id")
        category = row.get("category")
        goal = row.get("goal")
        expected_effect = row.get("expected_effect")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (sample_id, category, goal, expected_effect)
        ):
            raise ContractViolation("grounding annotation identity and semantics are required")
        assert isinstance(sample_id, str)
        assert isinstance(category, str)
        if sample_id in ids:
            raise ContractViolation(f"duplicate grounding sample id: {sample_id}")
        ids.add(sample_id)
        try:
            expected_kind = DecisionKind(str(row["expected_kind"]))
            goal_status = GoalStatus(str(row["goal_status"]))
            expected_action = (
                None
                if row.get("expected_action_kind") is None
                else GuiActionKind(str(row["expected_action_kind"]))
            )
            forbidden_decisions = frozenset(
                DecisionKind(value)
                for value in _string_list(row.get("forbidden_kinds", []), "forbidden_kinds")
            )
            forbidden_actions = frozenset(
                GuiActionKind(value)
                for value in _string_list(
                    row.get("forbidden_action_kinds", []), "forbidden_action_kinds"
                )
            )
        except (KeyError, ValueError) as exc:
            raise ContractViolation(f"invalid grounding annotation enum: {exc}") from exc
        expected_box = (
            None
            if row.get("expected_bbox") is None
            else _box(row["expected_bbox"], "expected_bbox")
        )
        forbidden_boxes_raw = row.get("forbidden_boxes", [])
        if not isinstance(forbidden_boxes_raw, list):
            raise ContractViolation("forbidden_boxes must be a list")
        forbidden_boxes = tuple(
            _box(value, "forbidden_boxes item") for value in forbidden_boxes_raw
        )
        if expected_kind == DecisionKind.ACT:
            if expected_action not in _QUALIFIED_ACTION_KINDS:
                raise ContractViolation("ACT annotation requires click, key, or hotkey")
            if (expected_action == GuiActionKind.CLICK) != (expected_box is not None):
                raise ContractViolation("click annotation requires a bbox; key actions forbid one")
        elif expected_action is not None or expected_box is not None:
            raise ContractViolation("non-ACT annotation cannot carry an action or bbox")
        if (expected_kind == DecisionKind.DONE) != (goal_status == GoalStatus.SUCCEEDED):
            raise ContractViolation("only DONE annotations may report a succeeded goal")
        frame_records.update(_frame_digest(source.parent, row.get("frames")))
        sample = GroundingSample(
            sample_id,
            expected_kind,
            _string_list(row.get("ocr_truth"), "ocr_truth"),
            expected_box,
            forbidden_decisions,
            category,
            expected_action,
            forbidden_actions,
            forbidden_boxes,
        )
        samples.append(sample)
        categories[category] += 1
    if require_qualification_volume:
        if len(samples) < sum(GROUNDING_CATEGORY_MINIMUMS.values()):
            raise ContractViolation("grounding qualification requires at least 200 samples")
        for category, minimum in GROUNDING_CATEGORY_MINIMUMS.items():
            if categories[category] < minimum:
                raise ContractViolation(
                    f"grounding category {category} requires at least {minimum} samples"
                )
    frame_set = hashlib.sha256("\n".join(sorted(frame_records)).encode("utf-8")).hexdigest()
    return GroundingCorpus(
        tuple(samples),
        tuple(sample.sample_id for sample in samples),
        tuple(sorted(categories.items())),
        frame_set,
    )


def load_grounding_predictions(
    path: str | Path,
    *,
    sample_ids: tuple[str, ...],
    source_revision: str,
) -> tuple[GroundingPrediction | None, ...]:
    validate_source_revision(source_revision)
    source = Path(path).resolve()
    predictions: dict[str, GroundingPrediction | None] = {}
    for row in _rows(source, "grounding predictions JSONL"):
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id.strip():
            raise ContractViolation("grounding prediction requires a sample id")
        if sample_id in predictions:
            raise ContractViolation(f"duplicate grounding prediction id: {sample_id}")
        if row.get("source_revision") != source_revision:
            raise ContractViolation("grounding prediction source revision mismatch")
        if not isinstance(row.get("model_id"), str) or not str(row["model_id"]).strip():
            raise ContractViolation("grounding prediction requires a model id")
        schema_valid = row.get("schema_valid")
        if type(schema_valid) is not bool:
            raise ContractViolation("grounding schema_valid must be a JSON boolean")
        if not schema_valid:
            predictions[sample_id] = None
            continue
        try:
            kind = DecisionKind(str(row["kind"]))
            action_kind = (
                None
                if row.get("action_kind") is None
                else GuiActionKind(str(row["action_kind"]))
            )
        except (KeyError, ValueError) as exc:
            raise ContractViolation(f"invalid grounding prediction enum: {exc}") from exc
        wrong_window = row.get("wrong_window", False)
        if type(wrong_window) is not bool:
            raise ContractViolation("grounding wrong_window must be a JSON boolean")
        target_box = (
            None
            if row.get("target_bbox") is None
            else _box(row["target_bbox"], "target_bbox")
        )
        if kind == DecisionKind.ACT:
            if action_kind not in _QUALIFIED_ACTION_KINDS:
                raise ContractViolation("valid ACT prediction requires click, key, or hotkey")
            if (action_kind == GuiActionKind.CLICK) != (target_box is not None):
                raise ContractViolation("valid click prediction requires a bbox; keys forbid one")
        elif action_kind is not None or target_box is not None:
            raise ContractViolation("valid non-ACT prediction cannot carry an action or bbox")
        predictions[sample_id] = GroundingPrediction(
            kind,
            _string_list(row.get("visible_text"), "visible_text"),
            target_box,
            action_kind,
            wrong_window,
        )
    expected = set(sample_ids)
    if set(predictions) != expected:
        missing = sorted(expected - set(predictions))
        extra = sorted(set(predictions) - expected)
        raise ContractViolation(
            f"grounding prediction cohort mismatch; missing={missing[:3]} extra={extra[:3]}"
        )
    return tuple(predictions[sample_id] for sample_id in sample_ids)


def _threshold_results(metrics: GroundingMetrics) -> tuple[dict[str, object], ...]:
    results: list[dict[str, object]] = []
    for metric, (operator, threshold) in GROUNDING_THRESHOLDS.items():
        observed = float(getattr(metrics, metric))
        passed = observed >= threshold if operator == ">=" else observed <= threshold
        results.append(
            {
                "metric": metric,
                "operator": operator,
                "threshold": threshold,
                "observed": observed,
                "passed": passed,
            }
        )
    return tuple(results)


def build_grounding_qualification_report(
    *,
    annotations_path: str | Path,
    predictions_path: str | Path,
    output_path: str | Path,
    source_revision: str,
    require_qualification_volume: bool = True,
) -> Path:
    validate_source_revision(source_revision)
    output = Path(output_path).resolve()
    annotations = Path(annotations_path).resolve()
    predictions = Path(predictions_path).resolve()
    for source, label in ((annotations, "annotations"), (predictions, "predictions")):
        if output.parent not in source.parents:
            raise ContractViolation(f"grounding {label} must be beneath the report directory")
    corpus = load_grounding_corpus(
        annotations, require_qualification_volume=require_qualification_volume
    )
    predicted = load_grounding_predictions(
        predictions,
        sample_ids=corpus.sample_ids,
        source_revision=source_revision,
    )
    metrics = GroundingEvaluator().evaluate(corpus.samples, predicted)
    thresholds = _threshold_results(metrics)
    volume_passed = (
        len(corpus.samples) >= sum(GROUNDING_CATEGORY_MINIMUMS.values())
        and all(
            dict(corpus.categories).get(category, 0) >= minimum
            for category, minimum in GROUNDING_CATEGORY_MINIMUMS.items()
        )
    )
    payload = {
        "schema": "uga.vlm_grounding_qualification",
        "schema_version": "1.1",
        "source_revision": source_revision,
        "passed": volume_passed and all(bool(item["passed"]) for item in thresholds),
        "qualification_volume": volume_passed,
        "category_counts": dict(corpus.categories),
        "category_minimums": GROUNDING_CATEGORY_MINIMUMS,
        "annotations": annotations.relative_to(output.parent).as_posix(),
        "annotations_sha256": sha256_file_limited(
            annotations,
            DEFAULT_ARTIFACT_LIMITS.max_benchmark_jsonl_bytes,
            "grounding annotations",
        ),
        "predictions": predictions.relative_to(output.parent).as_posix(),
        "predictions_sha256": sha256_file_limited(
            predictions,
            DEFAULT_ARTIFACT_LIMITS.max_benchmark_jsonl_bytes,
            "grounding predictions",
        ),
        "frame_set_sha256": corpus.frame_set_sha256,
        "metrics": asdict(metrics),
        "thresholds": list(thresholds),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output
