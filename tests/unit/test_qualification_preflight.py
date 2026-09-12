from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from uga.core.errors import ContractViolation
from uga.dataset.manifest import DatasetLicense, DatasetManifest
from uga.release.manifest import GateStatus
from uga.release.preflight import (
    HostQualificationProbe,
    QualificationPreflight,
    build_qualification_preflight,
)
from uga.release.qualification import (
    REQUIRED_GATE_IDS,
    QualificationLedger,
    QualificationRecord,
    build_qualified_release_manifest,
    hash_evidence,
)
from uga.training.artifact import TrainingArtifactManifest, sha256_file


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
            self.assertIn("verified training artifact was not supplied", report.blockers)
            path = report.write(root / "preflight.json")
            self.assertEqual(QualificationPreflight.load(path), report)
            self.assertEqual(report.ledger_sha256, ledger.canonical_sha256())

    def test_preflight_blocks_a_dirty_source_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = build_qualification_preflight(
                QualificationLedger.initialize("revision-1"),
                root,
                host=HostQualificationProbe("revision-1", (), False),
            )
            self.assertFalse(report.worktree_clean)
            self.assertIn("source worktree is not clean", report.blockers)

    def test_promotion_recomputes_rules_instead_of_trusting_deleted_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            revision = "a" * 40
            root = Path(temporary)
            project = root / "project"
            evidence = root / "evidence"
            bundle = root / "bundle"
            for directory in (project, evidence, bundle / "native"):
                directory.mkdir(parents=True)
            (project / "LICENSE").write_text("fixture", encoding="utf-8")
            (evidence / "LICENSE").write_text("fixture", encoding="utf-8")
            artifacts = hash_evidence(evidence, ("LICENSE",))
            ledger = QualificationLedger(
                revision,
                tuple(
                    QualificationRecord(
                        gate_id,
                        GateStatus.PASSED,
                        "fixture",
                        artifacts,
                        revision,
                    )
                    for gate_id in REQUIRED_GATE_IDS
                ),
            )
            ledger.write(evidence / "qualification.json")
            dataset = DatasetManifest(
                "dataset-v1",
                "1.0",
                revision,
                (),
                (
                    DatasetLicense(
                        "fixture-license",
                        "developer-owned fixture",
                        "project-owner-controlled",
                        True,
                        True,
                        "2026-09-09",
                    ),
                ),
                (),
            )
            dataset_path = dataset.write(root / "dataset-manifest.json")
            model = root / "model.json"
            config = root / "training.yaml"
            samples = root / "samples.jsonl"
            model.write_text("{}", encoding="utf-8")
            config.write_text("stage: motor_bc\n", encoding="utf-8")
            samples.write_text("{}\n", encoding="utf-8")
            artifact = TrainingArtifactManifest(
                "artifact-v1",
                model.name,
                "1.1",
                str(dataset_path),
                revision,
                str(config),
                str(samples),
                "fixture",
                sha256_file(model),
                sha256_file(dataset_path),
                sha256_file(config),
                sha256_file(samples),
                (("loss", 0.0),),
                (
                    ("dataset:fixture-license:source", "developer-owned fixture"),
                    ("dataset:fixture-license:license", "project-owner-controlled"),
                    ("dataset:fixture-license:distribution_allowed", "true"),
                    ("dataset:fixture-license:commercial_allowed", "true"),
                    ("dataset:fixture-license:review_date", "2026-09-09"),
                ),
            )
            artifact_path = artifact.write(root / "training-artifact.json")
            host = HostQualificationProbe(revision, (), True)
            report_path = build_qualification_preflight(
                ledger,
                project,
                host=host,
                training_artifact_path=artifact_path,
                dataset_root=root,
            ).write(root / "preflight.json")
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertIn("dataset contains less than the required 5 hours", payload["blockers"])
            payload["blockers"] = []
            report_path.write_text(json.dumps(payload), encoding="utf-8")
            (bundle / "package.whl").write_bytes(b"wheel")
            (bundle / "package.tar.gz").write_bytes(b"source")
            (bundle / "native" / "uga_capture.dll").write_bytes(b"native")
            (bundle / "third-party-inventory.json").write_text("{}", encoding="utf-8")

            with (
                patch("uga.release.preflight.probe_host", return_value=host),
                self.assertRaisesRegex(ContractViolation, "does not match"),
            ):
                build_qualified_release_manifest(
                    bundle,
                    ledger,
                    evidence,
                    version="1.0.0",
                    preflight_path=report_path,
                    project_root=project,
                )

    def test_preflight_enforces_dataset_rights_and_artifact_license_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "LICENSE").write_text("fixture", encoding="utf-8")
            dataset = DatasetManifest(
                "dataset-v1",
                "1.0",
                "revision-1",
                (),
                (
                    DatasetLicense(
                        "fixture-license",
                        "developer-owned fixture",
                        "project-owner-controlled",
                        False,
                        False,
                        "2026-09-09",
                    ),
                ),
                (),
            )
            dataset_path = dataset.write(root / "dataset-manifest.json")
            model = root / "model.json"
            config = root / "training.yaml"
            samples = root / "samples.jsonl"
            model.write_text("{}", encoding="utf-8")
            config.write_text("stage: motor_bc\n", encoding="utf-8")
            samples.write_text("{}\n", encoding="utf-8")
            artifact = TrainingArtifactManifest(
                "artifact-v1",
                model.name,
                "1.1",
                str(dataset_path),
                "revision-1",
                str(config),
                str(samples),
                "fixture",
                sha256_file(model),
                sha256_file(dataset_path),
                sha256_file(config),
                sha256_file(samples),
                (("loss", 0.0),),
                (
                    ("dataset:fixture-license:source", "developer-owned fixture"),
                    (
                        "dataset:fixture-license:license",
                        "project-owner-controlled",
                    ),
                    ("dataset:fixture-license:distribution_allowed", "false"),
                    ("dataset:fixture-license:commercial_allowed", "false"),
                    ("dataset:fixture-license:review_date", "2026-09-09"),
                    ("base_model", "fixture-only"),
                ),
            )
            artifact_path = artifact.write(root / "training-artifact.json")
            report = build_qualification_preflight(
                QualificationLedger.initialize("revision-1"),
                root,
                host=HostQualificationProbe("revision-1", ()),
                training_artifact_path=artifact_path,
                dataset_root=root,
            )

            self.assertEqual(report.training_artifact_id, "artifact-v1")
            self.assertIn(
                "dataset license does not allow release distribution: fixture-license",
                report.blockers,
            )
            self.assertIn(
                "dataset license does not allow commercial use: fixture-license",
                report.blockers,
            )

    def test_preflight_binds_training_and_dataset_revisions_to_release_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = DatasetManifest(
                "dataset-v1",
                "1.0",
                "old-revision",
                (),
                (
                    DatasetLicense(
                        "fixture-license",
                        "developer-owned fixture",
                        "project-owner-controlled",
                        True,
                        True,
                        "2026-09-09",
                    ),
                ),
                (),
            )
            dataset_path = dataset.write(root / "dataset-manifest.json")
            model = root / "model.json"
            config = root / "training.yaml"
            samples = root / "samples.jsonl"
            model.write_text("{}", encoding="utf-8")
            config.write_text("stage: motor_bc\n", encoding="utf-8")
            samples.write_text("{}\n", encoding="utf-8")
            artifact = TrainingArtifactManifest(
                "artifact-v1",
                model.name,
                "1.1",
                str(dataset_path),
                "old-revision",
                str(config),
                str(samples),
                "fixture",
                sha256_file(model),
                sha256_file(dataset_path),
                sha256_file(config),
                sha256_file(samples),
                (("loss", 0.0),),
                (
                    ("dataset:fixture-license:source", "developer-owned fixture"),
                    (
                        "dataset:fixture-license:license",
                        "project-owner-controlled",
                    ),
                    ("dataset:fixture-license:distribution_allowed", "true"),
                    ("dataset:fixture-license:commercial_allowed", "true"),
                    ("dataset:fixture-license:review_date", "2026-09-09"),
                    ("base_model", "fixture-only"),
                ),
            )
            report = build_qualification_preflight(
                QualificationLedger.initialize("release-revision"),
                root,
                host=HostQualificationProbe("release-revision", ()),
                training_artifact_path=artifact.write(root / "training-artifact.json"),
                dataset_root=root,
            )

            self.assertIn(
                "training artifact source revision does not match qualification ledger",
                report.blockers,
            )
            self.assertIn(
                "Dataset Manifest source revision does not match qualification ledger",
                report.blockers,
            )


if __name__ == "__main__":
    unittest.main()
