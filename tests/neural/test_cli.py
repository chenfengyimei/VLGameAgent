from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("torch")

from tests.neural.helpers import corpus


def cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "apps.training", *args],
        text=True,
        capture_output=True,
        timeout=60,
        cwd=cwd,
        env={**os.environ, "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"},
    )


def test_cli_train_verify_test_evaluation_and_dirty_guard(tmp_path: Path) -> None:
    data = corpus(tmp_path / "data")
    project = tmp_path / "source"
    subprocess.run(
        [
            "git",
            "clone",
            "--no-hardlinks",
            "--quiet",
            str(Path(__file__).resolve().parents[2]),
            str(project),
        ],
        check=True,
        capture_output=True,
    )
    arguments = [
        "neural-motor",
        "--train-samples",
        str(data["train"]),
        "--validation-samples",
        str(data["validation"]),
        "--dataset-manifest",
        str(data["manifest"]),
        "--dataset-root",
        str(data["root"]),
        "--config",
        str(data["config"]),
        "--output",
        str(tmp_path / "model"),
        "--policy-version",
        "cli-contract",
        "--encoder-version",
        "test-encoder/v1",
        "--encoder-license",
        "MIT",
        "--project-root",
        str(project),
    ]
    (project / "uncommitted.txt").write_text("dirty")
    failure = cli(*arguments, cwd=project)
    assert failure.returncode == 2 and "clean" in failure.stderr
    (project / "uncommitted.txt").unlink()
    result = cli(*arguments, cwd=project)
    assert result.returncode == 0, result.stderr
    artifact = json.loads(result.stdout)["artifact"]
    verify = cli("neural-verify", artifact)
    assert verify.returncode == 0, verify.stderr
    assert json.loads(verify.stdout)["integrity_verified"] is True
    evaluate = cli(
        "neural-evaluate",
        artifact,
        "--samples",
        str(data["test"]),
        "--dataset-root",
        str(data["root"]),
        "--split",
        "test",
        "--output",
        str(tmp_path / "evaluation.json"),
    )
    assert evaluate.returncode == 0, evaluate.stderr
    assert json.loads((tmp_path / "evaluation.json").read_text())["split"] == "test"


def test_cli_cannot_claim_a_different_repository_revision(tmp_path: Path) -> None:
    data = corpus(tmp_path / "data")
    result = cli(
        "neural-motor",
        "--train-samples",
        str(data["train"]),
        "--validation-samples",
        str(data["validation"]),
        "--dataset-manifest",
        str(data["manifest"]),
        "--dataset-root",
        str(data["root"]),
        "--config",
        str(data["config"]),
        "--output",
        str(tmp_path / "model"),
        "--policy-version",
        "test",
        "--encoder-version",
        "test",
        "--encoder-license",
        "MIT",
        "--project-root",
        str(tmp_path),
    )
    assert result.returncode == 2 and "actual clean source" in result.stderr
    assert not (tmp_path / "model").exists()
