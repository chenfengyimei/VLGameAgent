from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from uga.core.errors import ContractViolation
from uga.release.control_qualification import (
    build_control_qualification_report,
    uipi_probe_passed,
)
from uga.release.manifest import GateStatus
from uga.release.qualification import (
    QualificationLedger,
    QualificationRecord,
    hash_evidence,
)
from uga.windows.integrity import IntegrityLevel


class ControlQualificationTests(unittest.TestCase):
    def test_uipi_probe_requires_real_higher_integrity_rejection(self) -> None:
        self.assertTrue(
            uipi_probe_passed(
                IntegrityLevel.MEDIUM,
                IntegrityLevel.HIGH,
                executed=False,
                reason="integrity_incompatible",
            )
        )
        self.assertFalse(
            uipi_probe_passed(
                IntegrityLevel.MEDIUM,
                IntegrityLevel.MEDIUM,
                executed=False,
                reason="integrity_incompatible",
            )
        )
        self.assertFalse(
            uipi_probe_passed(
                IntegrityLevel.MEDIUM,
                IntegrityLevel.HIGH,
                executed=True,
                reason="executed",
            )
        )

    def test_control_report_binds_fixture_and_uipi_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            revision = "a" * 40
            fixture = root / "fixture.json"
            uipi = root / "uipi.json"
            fixture.write_text(
                json.dumps(
                    {
                        "schema": "uga.fixture_qualification",
                        "schema_version": "1.1",
                        "source_revision": revision,
                        "passed": True,
                        "control": {
                            name: {
                                "exercised": True,
                                "registered": True,
                                "passed": True,
                            }
                            for name in (
                                "focus_loss",
                                "held_key_fault",
                                "emergency_hotkey",
                                "watchdog_timeout",
                            )
                        },
                    }
                ),
                encoding="utf-8",
            )
            uipi.write_text(
                json.dumps(
                    {
                        "schema": "uga.uipi_qualification",
                        "schema_version": "1.1",
                        "source_revision": revision,
                        "passed": True,
                        "current_integrity": {"name": "MEDIUM", "value": 8192},
                        "target_integrity": {"name": "HIGH", "value": 12288},
                        "executed": False,
                        "reason": "integrity_incompatible",
                        "probe_action": "F24 key-up only",
                    }
                ),
                encoding="utf-8",
            )
            report = build_control_qualification_report(
                fixture_report_path=fixture,
                uipi_report_path=uipi,
                output_path=root / "control.json",
                source_revision=revision,
            )
            ledger = QualificationLedger.initialize(revision).with_record(
                QualificationRecord(
                    "control-hardware",
                    GateStatus.PASSED,
                    "supervised control matrix passed",
                    hash_evidence(root, ("control.json", "fixture.json", "uipi.json")),
                    revision,
                )
            )

            ledger.verify_artifacts(root)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["fixture_report"], "fixture.json")
            self.assertEqual(payload["uipi_report"], "uipi.json")

            uipi.write_text('{"tampered": true}', encoding="utf-8")
            with self.assertRaisesRegex(ContractViolation, "digest mismatch"):
                ledger.verify_artifacts(root)

            forged_uipi = {
                "schema": "uga.uipi_qualification",
                "schema_version": "1.1",
                "source_revision": revision,
                "passed": True,
                "current_integrity": {"name": "MEDIUM", "value": 8192},
                "target_integrity": {"name": "MEDIUM", "value": 8192},
                "executed": False,
                "reason": "integrity_incompatible",
                "probe_action": "F24 key-up only",
            }
            uipi.write_text(json.dumps(forged_uipi), encoding="utf-8")
            forged_digest = hashlib.sha256(uipi.read_bytes()).hexdigest()
            payload["uipi_report_sha256"] = forged_digest
            payload["exercises"]["uipi_mismatch"]["probe_report_sha256"] = forged_digest
            report.write_text(json.dumps(payload), encoding="utf-8")
            forged_ledger = QualificationLedger.initialize(revision).with_record(
                QualificationRecord(
                    "control-hardware",
                    GateStatus.PASSED,
                    "forged same-integrity control result",
                    hash_evidence(root, ("control.json", "fixture.json", "uipi.json")),
                    revision,
                )
            )
            with self.assertRaisesRegex(ContractViolation, "requires JSON evidence"):
                forged_ledger.verify_artifacts(root)

    def test_control_report_rejects_missing_fixture_exercise(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            revision = "a" * 40
            fixture = root / "fixture.json"
            uipi = root / "uipi.json"
            fixture.write_text(
                json.dumps(
                    {
                        "schema": "uga.fixture_qualification",
                        "schema_version": "1.1",
                        "source_revision": revision,
                        "passed": True,
                        "control": {"focus_loss": {"passed": True}},
                    }
                ),
                encoding="utf-8",
            )
            uipi.write_text(
                json.dumps(
                    {
                        "schema": "uga.uipi_qualification",
                        "schema_version": "1.1",
                        "source_revision": revision,
                        "passed": True,
                        "current_integrity": {"name": "MEDIUM", "value": 8192},
                        "target_integrity": {"name": "HIGH", "value": 12288},
                        "executed": False,
                        "reason": "integrity_incompatible",
                        "probe_action": "F24 key-up only",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ContractViolation, "incomplete Fixture exercise"):
                build_control_qualification_report(
                    fixture_report_path=fixture,
                    uipi_report_path=uipi,
                    output_path=root / "control.json",
                    source_revision=revision,
                )


if __name__ == "__main__":
    unittest.main()
