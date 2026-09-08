from __future__ import annotations

from dataclasses import dataclass

from uga.core.errors import ContractViolation
from uga.policy.action_chunk import ActionChunk


@dataclass(frozen=True, slots=True)
class OfflineMetrics:
    samples: int
    movement_mse: float
    movement_accuracy: float
    camera_mae: float
    camera_smoothness: float
    button_accuracy: float
    button_f1: float
    action_chunk_accuracy: float
    confidence_mae: float


@dataclass(frozen=True, slots=True)
class ClassificationMetrics:
    samples: int
    accuracy: float
    precision: float
    recall: float
    f1: float


class OfflineEvaluator:
    def __init__(self, *, axis_tolerance: float = 0.1) -> None:
        if not 0 <= axis_tolerance <= 2:
            raise ContractViolation("axis tolerance must be in [0, 2]")
        self._axis_tolerance = axis_tolerance

    def evaluate(
        self,
        predictions: tuple[ActionChunk, ...],
        targets: tuple[ActionChunk, ...],
    ) -> OfflineMetrics:
        if not predictions or len(predictions) != len(targets):
            raise ContractViolation("offline evaluation requires paired non-empty chunks")
        movement_error = 0.0
        movement_correct = 0
        camera_error = 0.0
        smoothness = 0.0
        button_correct = 0
        button_true_positive = 0
        button_false_positive = 0
        button_false_negative = 0
        chunks_correct = 0
        ticks = 0
        confidence_error = 0.0
        for prediction, target in zip(predictions, targets, strict=True):
            if prediction.horizon != target.horizon:
                raise ContractViolation("prediction and target horizons differ")
            chunk_correct = True
            for index in range(target.horizon):
                axis_errors = (
                    abs(prediction.move_x[index] - target.move_x[index]),
                    abs(prediction.move_y[index] - target.move_y[index]),
                    abs(prediction.look_x[index] - target.look_x[index]),
                    abs(prediction.look_y[index] - target.look_y[index]),
                )
                movement_error += axis_errors[0] ** 2 + axis_errors[1] ** 2
                movement_correct += max(axis_errors[:2]) <= self._axis_tolerance
                camera_error += axis_errors[2] + axis_errors[3]
                button_correct += prediction.buttons[index] == target.buttons[index]
                chunk_correct &= (
                    max(axis_errors) <= self._axis_tolerance
                    and prediction.buttons[index] == target.buttons[index]
                )
                predicted_mask = prediction.buttons[index]
                target_mask = target.buttons[index]
                button_true_positive += (predicted_mask & target_mask).bit_count()
                button_false_positive += (predicted_mask & ~target_mask & 0xFFFF).bit_count()
                button_false_negative += (~predicted_mask & target_mask & 0xFFFF).bit_count()
                ticks += 1
            chunks_correct += chunk_correct
            smoothness += self._smoothness(prediction.look_x) + self._smoothness(prediction.look_y)
            confidence_error += abs(prediction.confidence - target.confidence)
        return OfflineMetrics(
            len(predictions),
            movement_error / (2 * ticks),
            movement_correct / ticks,
            camera_error / (2 * ticks),
            smoothness / (2 * len(predictions)),
            button_correct / ticks,
            self._f1(button_true_positive, button_false_positive, button_false_negative),
            chunks_correct / len(predictions),
            confidence_error / len(predictions),
        )

    @staticmethod
    def classify(predictions: tuple[bool, ...], targets: tuple[bool, ...]) -> ClassificationMetrics:
        if not predictions or len(predictions) != len(targets):
            raise ContractViolation("classification evaluation requires paired non-empty labels")
        pairs = tuple(zip(predictions, targets, strict=True))
        true_positive = sum(prediction and target for prediction, target in pairs)
        false_positive = sum(prediction and not target for prediction, target in pairs)
        false_negative = sum(not prediction and target for prediction, target in pairs)
        accuracy = sum(prediction == target for prediction, target in pairs) / len(targets)
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 1.0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 1.0
        )
        return ClassificationMetrics(
            len(targets),
            accuracy,
            precision,
            recall,
            OfflineEvaluator._f1(true_positive, false_positive, false_negative),
        )

    @staticmethod
    def mode_accuracy(predictions: tuple[str, ...], targets: tuple[str, ...]) -> float:
        if not predictions or len(predictions) != len(targets):
            raise ContractViolation("mode evaluation requires paired non-empty labels")
        return sum(
            prediction == target for prediction, target in zip(predictions, targets, strict=True)
        ) / len(targets)

    @staticmethod
    def _f1(true_positive: int, false_positive: int, false_negative: int) -> float:
        denominator = 2 * true_positive + false_positive + false_negative
        return 2 * true_positive / denominator if denominator else 1.0

    @staticmethod
    def _smoothness(values: tuple[float, ...]) -> float:
        if len(values) < 2:
            return 0.0
        return sum(
            abs(current - previous) for previous, current in zip(values, values[1:], strict=False)
        ) / (len(values) - 1)
