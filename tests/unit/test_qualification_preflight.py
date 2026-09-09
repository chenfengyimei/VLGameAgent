from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from uga.dataset.manifest import DatasetLicense, DatasetManifest
from uga.release.preflight import HostQualificationProbe, build_qualification_preflight
from uga.release.qualification import QualificationLedger
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
            self.assertTrue(report.write(root / "preflight.json").is_file())

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
