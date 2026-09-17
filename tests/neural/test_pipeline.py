from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("torch")

from tests.neural.helpers import corpus
from uga.core.errors import ContractViolation
from uga.dataset.processor import DatasetSplit
from uga.policy.fast_policy import PolicyContext
from uga.time.clock import UGATime
from uga.training.artifact import sha256_file
from uga.training.neural_pipeline import (
    evaluate_neural_artifact,
    load_neural_fast_policy,
    train_neural_motor,
    verify_neural_artifact,
)


def run_train(data: dict[str, Path], output: Path) -> Path:
    return train_neural_motor(
        train_path=data["train"],
        validation_path=data["validation"],
        dataset_manifest_path=data["manifest"],
        dataset_root=data["root"],
        config_path=data["config"],
        output_directory=output,
        policy_version="test-neural",
        encoder_version="test-encoder/v1",
        encoder_license="MIT",
        source_revision="b" * 40,
    )


class Encoder:
    backbone_version = "test-encoder/v1"

    def encode(self, context):
        return (1.0, -1.0)


def test_training_verification_runtime_and_evaluation(tmp_path: Path) -> None:
    data = corpus(tmp_path / "data")
    artifact = run_train(data, tmp_path / "model")
    record = verify_neural_artifact(artifact)
    assert record["source_revision"] == "b" * 40 and record["dataset_source_revision"] == "a" * 40
    assert record["release_qualified"] is False
    with pytest.raises(ContractViolation, match="opt-in"):
        load_neural_fast_policy(artifact, Encoder(), expected_sha256=sha256_file(artifact))
    policy = load_neural_fast_policy(
        artifact, Encoder(), expected_sha256=sha256_file(artifact), allow_development_model=True
    )
    assert (
        policy.infer(PolicyContext("o", UGATime(100), (), None)).chunk.policy_version
        == "test-neural"
    )
    report = evaluate_neural_artifact(
        artifact_path=artifact,
        samples_path=data["test"],
        dataset_root=data["root"],
        split=DatasetSplit.TEST,
        output_path=tmp_path / "test-report.json",
    )
    assert json.loads(report.read_text())["metrics"]["samples"] == 4
    (artifact.parent / "model.json").write_text("{}")
    with pytest.raises(ContractViolation, match="digest"):
        verify_neural_artifact(artifact)


def test_heldout_test_contents_remain_closed(tmp_path: Path) -> None:
    data = corpus(tmp_path / "data")
    (data["root"] / "test" / "video.mp4").write_bytes(b"sealed")
    assert run_train(data, tmp_path / "model").is_file()


def test_wrong_validation_split_rejected(tmp_path: Path) -> None:
    data = corpus(tmp_path / "data")
    data["validation"].write_bytes(data["train"].read_bytes())
    with pytest.raises(ContractViolation, match="validation split"):
        run_train(data, tmp_path / "bad")
    assert not (tmp_path / "bad").exists() and not list(tmp_path.glob(".uga-neural-*"))


def test_changed_features_fail_before_training(tmp_path: Path) -> None:
    data = corpus(tmp_path / "data")
    rows = [json.loads(x) for x in data["train"].read_text().splitlines()]
    rows[0]["features"][0] += 0.25
    data["train"].write_text("\n".join(json.dumps(x) for x in rows) + "\n")
    with pytest.raises(ContractViolation, match="features do not match"):
        run_train(data, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_output_source_and_existing_directory_protection(tmp_path: Path) -> None:
    data = corpus(tmp_path / "data")
    with pytest.raises(ContractViolation, match="outside"):
        run_train(data, data["root"] / "train" / "model")
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ContractViolation, match="exist"):
        run_train(data, existing)
    assert existing.is_dir()


def test_wrong_encoder_or_pin(tmp_path: Path) -> None:
    data = corpus(tmp_path / "data")
    artifact = run_train(data, tmp_path / "model")
    with pytest.raises(ContractViolation, match="manifest digest"):
        verify_neural_artifact(artifact, expected_sha256="f" * 64)

    class WrongEncoder(Encoder):
        backbone_version = "wrong"

    with pytest.raises(ContractViolation, match="encoder identity"):
        load_neural_fast_policy(
            artifact,
            WrongEncoder(),
            expected_sha256=sha256_file(artifact),
            allow_development_model=True,
        )
    with pytest.raises(ContractViolation, match="trusted"):
        load_neural_fast_policy(
            artifact, Encoder(), expected_sha256=None, allow_development_model=True
        )


def test_test_eval_refuses_train_samples(tmp_path: Path) -> None:
    data = corpus(tmp_path / "data")
    artifact = run_train(data, tmp_path / "model")
    with pytest.raises(ContractViolation, match="test split"):
        evaluate_neural_artifact(
            artifact_path=artifact,
            samples_path=data["train"],
            dataset_root=data["root"],
            split=DatasetSplit.TEST,
            output_path=tmp_path / "report.json",
        )


def test_portable_artifact_without_original_sources(tmp_path: Path) -> None:
    import shutil

    data = corpus(tmp_path / "data")
    artifact = run_train(data, tmp_path / "model")
    shutil.move(str(artifact.parent), str(tmp_path / "relocated"))
    shutil.rmtree(data["root"])
    assert verify_neural_artifact(tmp_path / "relocated" / "artifact.json")["stage"] == "motor"


def test_failure_leaves_no_completed_artifact(tmp_path: Path, monkeypatch) -> None:
    from uga.training import neural_pipeline

    data = corpus(tmp_path / "data")

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic optimizer failure")

    monkeypatch.setattr(neural_pipeline, "train_neural_head", fail)
    with pytest.raises(RuntimeError, match="optimizer failure"):
        run_train(data, tmp_path / "model")
    assert not (tmp_path / "model").exists() and not list(tmp_path.glob(".uga-neural-*"))


def test_exact_verified_model_not_reread(tmp_path: Path, monkeypatch) -> None:
    from uga.training import neural_pipeline

    data = corpus(tmp_path / "data")
    artifact = run_train(data, tmp_path / "model")
    pinned = sha256_file(artifact)
    read = neural_pipeline.read_bytes_limited

    def replace_after_read(path, limit, label):
        result = read(path, limit, label)
        if Path(path) == artifact.parent / "model.json":
            Path(path).write_text("{}")
        return result

    monkeypatch.setattr(neural_pipeline, "read_bytes_limited", replace_after_read)
    policy = load_neural_fast_policy(
        artifact, Encoder(), expected_sha256=pinned, allow_development_model=True
    )
    assert policy.policy_version == "test-neural"
    with pytest.raises(ContractViolation, match="digest"):
        verify_neural_artifact(artifact)


def test_modified_metrics_with_updated_hash_rejected(tmp_path: Path) -> None:
    data = corpus(tmp_path / "data")
    artifact = run_train(data, tmp_path / "model")
    path = artifact.parent / "metrics.json"
    metrics = json.loads(path.read_text())
    metrics["validation"]["action_accuracy"] = 123
    path.write_text(json.dumps(metrics))
    payload = json.loads(artifact.read_text())
    payload["files"]["metrics.json"] = sha256_file(path)
    artifact.write_text(json.dumps(payload))
    with pytest.raises(ContractViolation, match="metrics do not match"):
        verify_neural_artifact(artifact)
