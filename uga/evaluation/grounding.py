from __future__ import annotations

from dataclasses import dataclass

from uga.core.errors import ContractViolation
from uga.perception.schema import DecisionKind, NormalizedBox


@dataclass(frozen=True, slots=True)
class GroundingSample:
    sample_id: str
    expected_kind: DecisionKind
    expected_text: tuple[str, ...]
    expected_box: NormalizedBox | None
    forbidden_kinds: frozenset[DecisionKind] = frozenset()

    def __post_init__(self) -> None:
        if not self.sample_id.strip():
            raise ContractViolation("grounding sample id cannot be blank")
        if self.expected_kind in self.forbidden_kinds:
            raise ContractViolation("expected grounding kind cannot be forbidden")


@dataclass(frozen=True, slots=True)
class GroundingPrediction:
    kind: DecisionKind
    visible_text: tuple[str, ...]
    target_box: NormalizedBox | None


@dataclass(frozen=True, slots=True)
class GroundingMetrics:
    samples: int
    schema_valid_rate: float
    text_f1: float
    action_kind_accuracy: float
    box_hit_rate: float
    median_center_error: float
    p95_center_error: float
    false_act_rate: float
    forbidden_action_count: int


def _tokens(values: tuple[str, ...]) -> set[str]:
    return {token for value in values for token in value.casefold().split() if token}


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(round((len(ordered) - 1) * fraction), len(ordered) - 1)
    return ordered[index]


class GroundingEvaluator:
    """Deterministic evaluator for annotated visual-grounding samples."""

    def evaluate(
        self,
        samples: tuple[GroundingSample, ...],
        predictions: tuple[GroundingPrediction | None, ...],
    ) -> GroundingMetrics:
        if not samples or len(samples) != len(predictions):
            raise ContractViolation("grounding evaluation requires paired non-empty samples")
        schema_valid = 0
        text_true_positive = 0
        text_false_positive = 0
        text_false_negative = 0
        kind_correct = 0
        box_hits = 0
        box_total = 0
        center_errors: list[float] = []
        false_acts = 0
        non_act_total = 0
        forbidden_actions = 0
        for sample, prediction in zip(samples, predictions, strict=True):
            if prediction is None:
                expected_tokens = _tokens(sample.expected_text)
                text_false_negative += len(expected_tokens)
                if sample.expected_box is not None:
                    box_total += 1
                continue
            schema_valid += 1
            predicted_tokens = _tokens(prediction.visible_text)
            expected_tokens = _tokens(sample.expected_text)
            text_true_positive += len(predicted_tokens & expected_tokens)
            text_false_positive += len(predicted_tokens - expected_tokens)
            text_false_negative += len(expected_tokens - predicted_tokens)
            kind_correct += prediction.kind == sample.expected_kind
            forbidden_actions += prediction.kind in sample.forbidden_kinds
            if sample.expected_kind != DecisionKind.ACT:
                non_act_total += 1
                false_acts += prediction.kind == DecisionKind.ACT
            if sample.expected_box is not None:
                box_total += 1
                if prediction.target_box is not None:
                    expected_center = sample.expected_box.center
                    predicted_center = prediction.target_box.center
                    error = (
                        (expected_center.x - predicted_center.x) ** 2
                        + (expected_center.y - predicted_center.y) ** 2
                    ) ** 0.5
                    center_errors.append(error)
                    box_hits += sample.expected_box.contains(predicted_center)
        text_denominator = (
            2 * text_true_positive + text_false_positive + text_false_negative
        )
        return GroundingMetrics(
            samples=len(samples),
            schema_valid_rate=schema_valid / len(samples),
            text_f1=(2 * text_true_positive / text_denominator if text_denominator else 1.0),
            action_kind_accuracy=kind_correct / len(samples),
            box_hit_rate=box_hits / box_total if box_total else 1.0,
            median_center_error=_percentile(center_errors, 0.5),
            p95_center_error=_percentile(center_errors, 0.95),
            false_act_rate=false_acts / non_act_total if non_act_total else 0.0,
            forbidden_action_count=forbidden_actions,
        )
