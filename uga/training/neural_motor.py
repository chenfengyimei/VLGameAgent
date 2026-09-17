"""Actual gradient motor BC, not instruction/recovery/reasoning/DAgger stages.

Train-only statistics and gradients; validation-only selection. Optional torch,
with explicit device choice. Numerical time budgets are cooperative, not a kill
boundary for a stuck GPU driver. No online input or automatic model downloads.
"""

from __future__ import annotations

import importlib
import math
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.policy.neural_motor import BUTTON_FLAGS, MAX_PARAMETERS, OUTPUT_DIM, NeuralMotorCheckpoint
from uga.training.behavior_cloning import MotorTrainingSample
from uga.training.neural_config import NeuralTrainingConfig

MAX_FEATURE_VALUES = 2_000_000
MAX_TRAINING_WORK = 500_000_000
MAX_EVALUATION_WORK = 50_000_000


@dataclass(frozen=True, slots=True)
class NeuralMetrics:
    samples: int
    loss: float
    movement_mse: float
    camera_huber: float
    button_accuracy: float
    button_f1: float
    action_accuracy: float
    abstention_rate: float


def evaluate_head(
    model: NeuralMotorCheckpoint, samples: tuple[MotorTrainingSample, ...]
) -> NeuralMetrics:
    if (
        not samples
        or len(samples) * model.input_dim > MAX_FEATURE_VALUES
        or len(samples) * model.parameter_count > MAX_EVALUATION_WORK
    ):
        raise ContractViolation("evaluation requires a bounded nonempty sample set")
    move = camera = bce = 0.0
    masks = correct = abstentions = tp = fp = fn = 0
    for sample in samples:
        raw, outside = model.raw_predict(sample.features)
        prediction = model.predict(sample.features)
        targets = (sample.move_x, sample.move_y, sample.look_x, sample.look_y)
        # Loss describes the raw network; deployed action metrics also apply
        # the support gate, so raw loss and action accuracy are not identical.
        axes = tuple(math.tanh(value) for value in raw[:4])
        move += sum((x - y) ** 2 for x, y in zip(axes[:2], targets[:2], strict=True)) / 2
        camera += sum(_huber(x - y) for x, y in zip(axes[2:], targets[2:], strict=True)) / 2
        for flag, logit in zip(BUTTON_FLAGS, raw[4:], strict=True):
            y = float(bool(sample.buttons & flag))
            bce += max(logit, 0.0) - logit * y + math.log1p(math.exp(-abs(logit)))
        abstentions += outside
        masks += not outside and prediction.buttons == sample.buttons
        correct += (
            not outside
            and prediction.buttons == sample.buttons
            and all(abs(x - y) <= 0.1 for x, y in zip(prediction.axes, targets, strict=True))
        )
        tp += (prediction.buttons & sample.buttons).bit_count()
        fp += (prediction.buttons & ~sample.buttons).bit_count()
        fn += (~prediction.buttons & sample.buttons).bit_count()
    n = len(samples)
    move /= n
    camera /= n
    loss = move + camera + bce / (n * len(BUTTON_FLAGS))
    denominator = 2 * tp + fp + fn
    metrics = NeuralMetrics(
        n,
        loss,
        move,
        camera,
        masks / n,
        2 * tp / denominator if denominator else 1.0,
        correct / n,
        abstentions / n,
    )
    if any(not math.isfinite(float(x)) for x in asdict(metrics).values()):
        raise ContractViolation("neural evaluation produced nonfinite metrics")
    return metrics


def _huber(error: float) -> float:
    # torch.nn.functional.smooth_l1_loss(beta=0.1)
    return 0.5 * error * error / 0.1 if abs(error) < 0.1 else abs(error) - 0.05


def validation_reliability(accuracy: float, count: int) -> float:
    """Wilson lower bound is an aggregate heuristic, not independent calibration."""
    p, z = accuracy, 1.96
    return max(
        0.0,
        (p + z * z / (2 * count) - z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)))
        / (1 + z * z / count),
    )


def torch_backend(device: str) -> Any:
    try:
        torch = importlib.import_module("torch")
    except ImportError as exc:
        raise BackendUnavailableError(
            "neural training requires the optional hash-locked PyTorch environment"
        ) from exc
    if device == "cuda" and not torch.cuda.is_available():
        raise BackendUnavailableError("CUDA was explicitly requested but is unavailable")
    return torch


def train_neural_head(
    train: tuple[MotorTrainingSample, ...],
    validation: tuple[MotorTrainingSample, ...],
    *,
    policy_version: str,
    encoder_version: str,
    config: NeuralTrainingConfig,
    cancelled: Callable[[], bool] = lambda: False,
) -> tuple[NeuralMotorCheckpoint, dict[str, Any]]:
    if not train or not validation:
        raise ContractViolation("separate nonempty train and validation sets are required")
    if any(not sample.has_provenance for sample in (*train, *validation)):
        raise ContractViolation("neural samples require complete provenance")
    if {s.episode_id for s in train} & {s.episode_id for s in validation}:
        raise ContractViolation("train/validation Episode leakage")
    dim = len(train[0].features)
    if any(len(s.features) != dim for s in (*train, *validation)):
        raise ContractViolation("neural sample feature dimensions differ")
    values = (len(train) + len(validation)) * dim
    params = config.hidden_dim * (dim + 1) + OUTPUT_DIM * (config.hidden_dim + 1)
    work = (len(train) + len(validation)) * config.epochs * params * 3
    if values > MAX_FEATURE_VALUES or params > MAX_PARAMETERS or work > MAX_TRAINING_WORK:
        raise ContractViolation("neural training exceeds feature, parameter or work budget")
    if any(abs(x) > 1e6 for sample in (*train, *validation) for x in sample.features):
        raise ContractViolation("neural features exceed numeric limits")
    columns = tuple(zip(*(sample.features for sample in train), strict=True))
    means = tuple(statistics.fmean(column) for column in columns)
    scales = tuple(max(1e-6, statistics.pstdev(column)) for column in columns)
    lower, upper = tuple(min(c) for c in columns), tuple(max(c) for c in columns)
    started = time.monotonic()

    def check_budget() -> None:
        if cancelled():
            raise ContractViolation("neural training cancelled; no completed artifact")
        if time.monotonic() - started > config.max_seconds:
            raise ContractViolation("neural training exceeded total wall-time budget")

    check_budget()
    torch = torch_backend(config.device)
    check_budget()
    device = torch.device(config.device)
    # Restore caller CPU RNG; batch order uses a private generator. Explicit
    # CPU/float32 allocation isolates ambient default device and dtype.
    with torch.random.fork_rng(devices=[]):
        torch.random.default_generator.manual_seed(config.seed)
        network = torch.nn.Sequential(
            torch.nn.Linear(dim, config.hidden_dim, device="cpu", dtype=torch.float32),
            torch.nn.Tanh(),
            torch.nn.Linear(config.hidden_dim, OUTPUT_DIM, device="cpu", dtype=torch.float32),
        )
    network = network.to(device)
    generator = torch.Generator(device="cpu").manual_seed(config.seed)
    optimizer = torch.optim.AdamW(
        network.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    normalized = [
        [
            max(-8.0, min(8.0, (x - m) / s))
            for x, m, s in zip(sample.features, means, scales, strict=True)
        ]
        for sample in train
    ]
    x = torch.tensor(normalized, dtype=torch.float32, device="cpu")
    y = torch.tensor(
        [
            [s.move_x, s.move_y, s.look_x, s.look_y]
            + [float(bool(s.buttons & flag)) for flag in BUTTON_FLAGS]
            for s in train
        ],
        dtype=torch.float32,
        device="cpu",
    )

    def snapshot() -> NeuralMotorCheckpoint:
        state = network.state_dict()
        return NeuralMotorCheckpoint(
            policy_version,
            encoder_version,
            dim,
            config.hidden_dim,
            means,
            scales,
            lower,
            upper,
            state["0.weight"].detach().cpu().tolist(),
            state["0.bias"].detach().cpu().tolist(),
            state["2.weight"].detach().cpu().tolist(),
            state["2.bias"].detach().cpu().tolist(),
        )

    initial = evaluate_head(snapshot(), validation)
    best, best_loss, best_epoch = snapshot(), initial.loss, 0
    history: list[dict[str, float | int]] = []
    for epoch in range(1, config.epochs + 1):
        check_budget()
        network.train()
        indices = torch.randperm(len(train), generator=generator, device="cpu")
        for offset in range(0, len(train), config.batch_size):
            check_budget()
            selected = indices[offset : offset + config.batch_size]
            xb, yb = x[selected].to(device), y[selected].to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = network(xb)
            axes = torch.tanh(logits[:, :4])
            loss = (
                torch.nn.functional.mse_loss(axes[:, :2], yb[:, :2])
                + torch.nn.functional.smooth_l1_loss(axes[:, 2:], yb[:, 2:4], beta=0.1)
                + torch.nn.functional.binary_cross_entropy_with_logits(logits[:, 4:], yb[:, 4:])
            )
            if not bool(torch.isfinite(loss).item()):
                raise ContractViolation("nonfinite neural training loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                network.parameters(), config.gradient_clip, error_if_nonfinite=True
            )
            optimizer.step()
        if config.device == "cuda":
            torch.cuda.synchronize(device)
        check_budget()
        candidate = snapshot()
        metrics = evaluate_head(candidate, validation)
        check_budget()
        history.append({"epoch": epoch, "validation_loss": metrics.loss})
        if metrics.loss < best_loss - 1e-9:
            best, best_loss, best_epoch = candidate, metrics.loss, epoch
        if epoch - best_epoch >= config.patience:
            break
    final_validation = evaluate_head(best, validation)
    final_train = evaluate_head(best, train)
    reliability = validation_reliability(final_validation.action_accuracy, len(validation))
    best = replace(best, validation_reliability=reliability)
    check_budget()
    return best, {
        "schema": "uga.neural_motor_training",
        "schema_version": "1.0",
        "stage": "motor",
        "trainer": "pytorch_mlp_motor_v1",
        "torch_version": str(torch.__version__),
        "device": str(device),
        "device_name": (
            str(torch.cuda.get_device_name(device)) if config.device == "cuda" else "cpu"
        ),
        "cuda_version": str(torch.version.cuda) if config.device == "cuda" else None,
        "seed": config.seed,
        "best_epoch": best_epoch,
        "completed_epochs": len(history),
        "elapsed_seconds": time.monotonic() - started,
        "parameter_count": params,
        "initial_validation": asdict(initial),
        "train": asdict(final_train),
        "validation": asdict(final_validation),
        "history": history,
        "confidence_method": "validation_action_accuracy_wilson_lower_times_button_margin",
        "release_qualified": False,
        "test_split_used": False,
    }
