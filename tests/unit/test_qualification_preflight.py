from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from uga.release.preflight import HostQualificationProbe, build_qualification_preflight
from uga.release.qualification import QualificationLedger


class QualificationPreflightTests(unittest.TestCase):
    def test_preflight_reports_missing_external_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ledger = QualificationLedger.initialize("workspace-unversioned")
            report = build_qualification_preflight(
                ledger,
                root,
                host=HostQualificationProbe(
                    None,
                    ("NVIDIA Fixture GPU, 8192 MiB, 1.0",),
                ),
            )
            self.assertFalse(report.ledger_releasable)
            self.assertEqual(len(report.gpu_devices), 1)
            self.assertIn("source tree has no traceable Git revision", report.blockers)
            self.assertIn("repository license has not been selected", report.blockers)
            self.assertTrue(report.write(root / "preflight.json").is_file())


if __name__ == "__main__":
    unittest.main()
