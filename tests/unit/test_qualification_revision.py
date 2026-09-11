from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from uga.core.errors import ContractViolation
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


if __name__ == "__main__":
    unittest.main()
