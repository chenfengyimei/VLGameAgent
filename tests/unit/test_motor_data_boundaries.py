from __future__ import annotations

import json
from pathlib import Path

import pytest

from uga.core.errors import ContractViolation
from uga.training.behavior_cloning import BehaviorCloningTrainer, MotorTrainingSample
from uga.training.motor_pipeline import export_motor_samples, load_motor_samples


def record() -> dict[str, object]:
    return dict(
        features=[1.0, 0.0],
        move_x=1.0,
        move_y=0.0,
        look_x=0.0,
        look_y=0.0,
        buttons=0,
        episode_id="e",
        observation_id="o",
        action_id="a",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("features", [True]),
        ("features", ["1"]),
        ("move_x", True),
        ("move_x", "1"),
        ("buttons", True),
        ("buttons", 0.5),
        ("episode_id", None),
        ("action_id", 3),
        ("observation_id", ""),
    ],
)
def test_motor_json_does_not_coerce_untrusted_values(
    tmp_path: Path, field: str, value: object
) -> None:
    row = record()
    row[field] = value
    path = tmp_path / "samples.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ContractViolation):
        load_motor_samples(path)


def test_duplicate_sample_keys_rejected(tmp_path: Path) -> None:
    path = tmp_path / "samples.jsonl"
    path.write_text(json.dumps(record())[:-1] + ',"buttons":1}\n', encoding="utf-8")
    with pytest.raises(ContractViolation):
        load_motor_samples(path)


@pytest.mark.parametrize("rate", [float("nan"), float("inf"), True])
def test_baseline_learning_rate_is_finite_and_numeric(rate: float) -> None:
    with pytest.raises(ContractViolation):
        BehaviorCloningTrainer().train(
            (MotorTrainingSample((1.0,), 1.0, 0.0, 0.0, 0.0, 0),),
            policy_version="test",
            learning_rate=rate,
        )


def test_motor_export_never_writes_source_episode(tmp_path: Path) -> None:
    from tests.integration.test_dataset_policy import make_episode

    source = make_episode(tmp_path)
    original = (source / "checksum.json").read_bytes()
    with pytest.raises(ContractViolation, match="outside"):
        export_motor_samples((source,), source / "checksum.json")
    assert (source / "checksum.json").read_bytes() == original


def test_duplicate_sources_preserve_output(tmp_path: Path) -> None:
    from tests.integration.test_dataset_policy import make_episode

    source = make_episode(tmp_path)
    output = tmp_path / "existing.jsonl"
    output.write_text("unchanged", encoding="utf-8")
    with pytest.raises(ContractViolation, match="duplicate"):
        export_motor_samples((source, source), output)
    assert output.read_text(encoding="utf-8") == "unchanged"
