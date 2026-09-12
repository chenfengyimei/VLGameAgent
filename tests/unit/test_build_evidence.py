from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from uga.core.errors import ContractViolation
from uga.release.build_evidence import (
    REQUIRED_BUILD_CHECKS,
    BuildQualificationReport,
    build_qualification_report,
)
from uga.release.development import build_development_manifest


class BuildEvidenceTests(unittest.TestCase):
    def _bundle(self, root: Path, revision: str = "a" * 40) -> Path:
        bundle = root / "uga-test-bundle"
        (bundle / "native").mkdir(parents=True)
        (bundle / "package.whl").write_bytes(b"wheel")
        (bundle / "package.tar.gz").write_bytes(b"source")
        (bundle / "native" / "uga_capture.dll").write_bytes(b"native")
        (bundle / "run_uga.ps1").write_text("Write-Output uga", encoding="utf-8")
        (bundle / "third-party-inventory.json").write_text(
            json.dumps(
                {
                    "schema": "uga.dependency_inventory",
                    "schema_version": "1.1",
                    "license_declarations_complete": True,
                    "unknown_license_declarations": [],
                }
            ),
            encoding="utf-8",
        )
        build_development_manifest(
            bundle,
            source_revision=revision,
            package_smoke_passed=True,
        ).write(bundle / "release-manifest.json")
        return bundle

    def test_report_binds_every_critical_build_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle(root)
            report = build_qualification_report(
                bundle,
                source_revision="a" * 40,
                launcher_smoke_passed=True,
            )
            report_path = report.write(root / "build-qualification.json")

            loaded = BuildQualificationReport.load(report_path)
            loaded.verify(bundle)
            self.assertEqual(loaded.checks, REQUIRED_BUILD_CHECKS)
            self.assertEqual(len(loaded.artifacts), 6)
            self.assertTrue(loaded.passed)

            (bundle / "package.whl").write_bytes(b"tampered")
            with self.assertRaisesRegex(ContractViolation, "digest mismatch"):
                loaded.verify(bundle)

    def test_report_requires_real_launcher_smoke_and_matching_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))
            with self.assertRaisesRegex(ContractViolation, "launcher smoke"):
                build_qualification_report(
                    bundle,
                    source_revision="a" * 40,
                    launcher_smoke_passed=False,
                )
            with self.assertRaisesRegex(ContractViolation, "source revision"):
                build_qualification_report(
                    bundle,
                    source_revision="b" * 40,
                    launcher_smoke_passed=True,
                )

    def test_report_rejects_incomplete_dependency_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))
            inventory = bundle / "third-party-inventory.json"
            payload = json.loads(inventory.read_text(encoding="utf-8"))
            payload["license_declarations_complete"] = False
            payload["unknown_license_declarations"] = ["python:unknown@1"]
            inventory.write_text(json.dumps(payload), encoding="utf-8")
            build_development_manifest(
                bundle,
                source_revision="a" * 40,
                package_smoke_passed=True,
            ).write(bundle / "release-manifest.json")

            with self.assertRaisesRegex(ContractViolation, "license declarations"):
                build_qualification_report(
                    bundle,
                    source_revision="a" * 40,
                    launcher_smoke_passed=True,
                )


if __name__ == "__main__":
    unittest.main()
