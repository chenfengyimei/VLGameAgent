from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from uga.core.errors import ContractViolation
from uga.dataset.manifest import DatasetLicense, DatasetManifest
from uga.release.manifest import GateStatus
from uga.release.model_qualification import (
    MODEL_STAGES,
    build_model_qualification_report,
    build_model_stage_report,
)
from uga.release.qualification import QualificationLedger, QualificationRecord, hash_evidence
from uga.training.artifact import TrainingArtifactManifest, sha256_file


class ModelQualificationTests(unittest.TestCase):
    def test_aggregate_recursively_verifies_all_stage_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            revision = "a" * 40
            dataset_path = self._dataset(root, revision)
            stage_reports = tuple(
                self._stage(root, dataset_path, revision, stage) for stage in MODEL_STAGES
            )
            aggregate = build_model_qualification_report(
                dataset_manifest_path=dataset_path,
                stage_report_paths=stage_reports,
                output_path=root / "model-qualification.json",
                source_revision=revision,
            )
            ledger = QualificationLedger.initialize(revision).with_record(
                QualificationRecord(
                    "model-training",
                    GateStatus.PASSED,
                    "five GPU stages passed",
                    hash_evidence(root, (aggregate.name,)),
                    revision,
                )
            )
            ledger.verify_artifacts(root)

            offline = root / "motor" / "offline.json"
            payload = json.loads(offline.read_text(encoding="utf-8"))
            payload["metrics"]["score"] = 0.1
            offline.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ContractViolation, "bound to its source revision"):
                ledger.verify_artifacts(root)

    def test_stage_report_recomputes_declared_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            revision = "a" * 40
            dataset_path = self._dataset(root, revision)
            stage_root, artifact_path = self._artifact(root, dataset_path, revision, "motor")
            artifact_digest = sha256_file(artifact_path)
            offline = self._metrics(
                stage_root / "offline.json",
                "uga.offline_metrics",
                "motor",
                revision,
                sha256_file(dataset_path),
                artifact_digest,
                passed_score=0.1,
            )
            closed = self._metrics(
                stage_root / "closed.json",
                "uga.closed_loop_metrics",
                "motor",
                revision,
                sha256_file(dataset_path),
                artifact_digest,
            )
            with self.assertRaisesRegex(ContractViolation, "threshold check did not pass"):
                build_model_stage_report(
                    stage="motor",
                    dataset_manifest_path=dataset_path,
                    artifact_manifest_path=artifact_path,
                    offline_metrics_path=offline,
                    closed_loop_metrics_path=closed,
                    trainer_backend="fixture-gpu-trainer",
                    training_run_id="run-motor",
                    output_path=root / "motor-stage.json",
                    source_revision=revision,
                    gpu_devices=("NVIDIA Fixture GPU",),
                )

    def _stage(
        self, root: Path, dataset_path: Path, revision: str, stage: str
    ) -> Path:
        stage_root, artifact_path = self._artifact(root, dataset_path, revision, stage)
        artifact_digest = sha256_file(artifact_path)
        dataset_digest = sha256_file(dataset_path)
        offline = self._metrics(
            stage_root / "offline.json",
            "uga.offline_metrics",
            stage,
            revision,
            dataset_digest,
            artifact_digest,
        )
        closed = self._metrics(
            stage_root / "closed.json",
            "uga.closed_loop_metrics",
            stage,
            revision,
            dataset_digest,
            artifact_digest,
        )
        return build_model_stage_report(
            stage=stage,
            dataset_manifest_path=dataset_path,
            artifact_manifest_path=artifact_path,
            offline_metrics_path=offline,
            closed_loop_metrics_path=closed,
            trainer_backend="fixture-gpu-trainer",
            training_run_id=f"run-{stage}",
            output_path=root / f"{stage}-stage.json",
            source_revision=revision,
            gpu_devices=("NVIDIA Fixture GPU",),
        )

    @staticmethod
    def _dataset(root: Path, revision: str) -> Path:
        return DatasetManifest(
            "fixture-dataset",
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
                    "2026-09-12",
                ),
            ),
            (),
        ).write(root / "dataset.json")

    @staticmethod
    def _artifact(
        root: Path, dataset_path: Path, revision: str, stage: str
    ) -> tuple[Path, Path]:
        stage_root = root / stage
        stage_root.mkdir()
        model = stage_root / "model.bin"
        config = stage_root / "config.yaml"
        samples = stage_root / "samples.jsonl"
        model.write_bytes(stage.encode("utf-8"))
        config.write_text(f"stage: {stage}\n", encoding="utf-8")
        samples.write_text("{}\n", encoding="utf-8")
        artifact = TrainingArtifactManifest(
            f"{stage}-artifact",
            model.name,
            "1.1",
            str(dataset_path),
            revision,
            str(config),
            str(samples),
            "fixture-model",
            sha256_file(model),
            sha256_file(dataset_path),
            sha256_file(config),
            sha256_file(samples),
            (("loss", 0.1),),
            (("base_model", "fixture-only"),),
        )
        return stage_root, artifact.write(stage_root / "artifact.json")

    @staticmethod
    def _metrics(
        path: Path,
        schema: str,
        stage: str,
        revision: str,
        dataset_digest: str,
        artifact_digest: str,
        *,
        passed_score: float = 0.9,
    ) -> Path:
        payload = {
            "schema": schema,
            "schema_version": "1.1",
            "source_revision": revision,
            "stage": stage,
            "dataset_manifest_sha256": dataset_digest,
            "artifact_manifest_sha256": artifact_digest,
            "passed": True,
            "metrics": {"score": passed_score},
            "checks": [{"metric": "score", "operator": ">=", "threshold": 0.8}],
        }
        if schema == "uga.offline_metrics":
            payload["samples"] = 10
        else:
            payload["episodes"] = 4
            payload["games"] = ["fixture"]
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
