from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_cpu_locks_use_one_pinned_index_and_hashes() -> None:
    for platform in ("linux", "win32"):
        text = (ROOT / f"requirements-neural-cpu-{platform}.txt").read_text()
        assert "--index-url https://download.pytorch.org/whl/cpu" in text
        assert "--extra-index-url" not in text
        assert "torch==2.10.0+cpu --hash=sha256:" in text
        entries = [s for s in text.splitlines() if s and not s.startswith(("#", "--"))]
        assert len(entries) == 9
        for line in entries:
            assert re.fullmatch(r"[\w.-]+==[\w.+-]+ --hash=sha256:[0-9a-f]{64}", line)


def test_neural_ci_cannot_silently_skip_missing_backend() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/neural-ci.yml").read_text())
    runs = [s["run"] for s in workflow["jobs"]["neural"]["steps"] if "run" in s]
    assert next(i for i, r in enumerate(runs) if "import torch" in r) < next(
        i for i, r in enumerate(runs) if "pytest tests/neural" in r
    )
    assert len(workflow["jobs"]["neural"]["strategy"]["matrix"]["include"]) == 2


def test_portable_loader_and_help_do_not_import_torch() -> None:
    script = """
import sys
from uga.policy.neural_motor import NeuralMotorCheckpoint
from uga.training.neural_pipeline import verify_neural_artifact
assert 'torch' not in sys.modules
from apps.training.__main__ import main
sys.argv = ['uga-train', '--help']
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert "neural-motor" in result.stdout and "neural-evaluate" in result.stdout
