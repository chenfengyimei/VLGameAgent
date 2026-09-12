from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from apps.training.__main__ import _motor
from uga.core.errors import ContractViolation
from uga.release.fixture_process import launch_owned_python_gui
from uga.release.revision import require_clean_source_revision


class QualificationRevisionTests(unittest.TestCase):
    def test_clean_checkout_returns_full_head(self) -> None:
        revision = "a" * 40
        results = (
            subprocess.CompletedProcess(("git", "status"), 0, stdout="", stderr=""),
            subprocess.CompletedProcess(("git", "rev-parse"), 0, stdout=revision + "\n", stderr=""),
        )
        with patch("uga.release.revision.subprocess.run", side_effect=results):
            self.assertEqual(require_clean_source_revision(Path(".")), revision)

    def test_dirty_checkout_is_rejected(self) -> None:
        result = subprocess.CompletedProcess(
            ("git", "status"), 0, stdout=" M changed.py\n", stderr=""
        )
        with (
            patch("uga.release.revision.subprocess.run", return_value=result),
            self.assertRaisesRegex(ContractViolation, "clean committed"),
        ):
            require_clean_source_revision(Path("."))

    def test_abbreviated_revision_is_rejected(self) -> None:
        results = (
            subprocess.CompletedProcess(("git", "status"), 0, stdout="", stderr=""),
            subprocess.CompletedProcess(("git", "rev-parse"), 0, stdout="abc123\n", stderr=""),
        )
        with (
            patch("uga.release.revision.subprocess.run", side_effect=results),
            self.assertRaisesRegex(ContractViolation, "full Git"),
        ):
            require_clean_source_revision(Path("."))

    def test_training_cli_rejects_revision_other_than_clean_head(self) -> None:
        args = Namespace(project_root=Path("."), source_revision="b" * 40)
        with (
            patch("apps.training.__main__.require_clean_source_revision", return_value="a" * 40),
            patch("apps.training.__main__.train_motor_policy") as train,
            self.assertRaisesRegex(ContractViolation, "does not match clean Git HEAD"),
        ):
            _motor(args)
        train.assert_not_called()

    def test_corpus_checks_revision_before_creating_output(self) -> None:
        from uga.release.fixture_corpus import run_fixture_corpus

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "must-not-exist"
            with (
                patch(
                    "uga.release.fixture_corpus.require_clean_source_revision",
                    side_effect=ContractViolation("dirty"),
                ),
                self.assertRaisesRegex(ContractViolation, "dirty"),
            ):
                run_fixture_corpus(
                    project_root=Path("."),
                    output_root=output,
                    train_duration_seconds=6000,
                    test_duration_seconds=600,
                    target_fps=3,
                    backend="gdi_fallback",
                    allow_physical_input=True,
                )
            self.assertFalse(output.exists())

    def test_gui_launcher_uses_base_interpreter_pid_and_propagates_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "python.exe"
            pythonw = root / "pythonw.exe"
            base.touch()
            pythonw.touch()
            sentinel = object()
            with (
                patch(
                    "uga.release.fixture_process.sys._base_executable",
                    str(base),
                    create=True,
                ),
                patch(
                    "uga.release.fixture_process.subprocess.Popen",
                    return_value=sentinel,
                ) as popen,
            ):
                launched = launch_owned_python_gui(("-m", "apps.example_game"), cwd=root)

            self.assertIs(launched, sentinel)
            command = popen.call_args.args[0]
            environment = popen.call_args.kwargs["env"]
            self.assertEqual(command[0], str(pythonw.resolve()))
            self.assertIn(str(root.resolve()), environment["PYTHONPATH"].split(os.pathsep))


if __name__ == "__main__":
    unittest.main()
