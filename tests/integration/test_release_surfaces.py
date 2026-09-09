from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast

from tests.integration.test_dataset_policy import make_episode
from uga.benchmark.fixture import fixture_environments, verified_fixture_environments
from uga.benchmark.io import load_benchmark_runs, write_benchmark_report, write_benchmark_runs
from uga.benchmark.runner import BenchmarkRunner
from uga.benchmark.schema import BenchmarkRun, BenchmarkSplit, BenchmarkTask, load_benchmark_tasks
from uga.control.lease import ControlMode
from uga.core.errors import ContractViolation
from uga.dashboard.controller import DashboardCommandRouter
from uga.dashboard.state import DashboardCommand, DashboardState, render_dashboard
from uga.policy.fast_policy import DecoderCheckpoint
from uga.recording.debugger import write_replay_debugger
from uga.release.development import build_development_manifest
from uga.release.manifest import GateStatus, ReleaseGate, ReleaseManifest, hash_artifacts
from uga.release.qualification import (
    EvidenceArtifact,
    QualificationLedger,
    QualificationRecord,
    hash_evidence,
)
from uga.training.artifact import TrainingArtifactManifest, sha256_file


class FakeBenchmarkEnvironment:
    def run(self, task: BenchmarkTask, repetition: int) -> BenchmarkRun:
        return BenchmarkRun(
            task.task_id,
            task.game_id,
            repetition,
            True,
            10.0,
            0,
            1,
            1,
            False,
            0,
            0,
            0,
            10,
            0.1,
            (2.0, 3.0),
            (10.0, 12.0),
            (4.0, 5.0),
            (20.0, 22.0),
            8.0,
            task.split,
        )


class FakeOperatorControl:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def start(self) -> None:
        self.commands.append("start")

    def pause(self) -> None:
        self.commands.append("pause")

    def resume(self) -> None:
        self.commands.append("resume")

    def stop(self) -> None:
        self.commands.append("stop")

    def take_control(self) -> None:
        self.commands.append("take_control")

    def release_control(self) -> None:
        self.commands.append("release_control")

    def emergency_release(self) -> None:
        self.commands.append("emergency_release")


class ReleaseSurfaceTests(unittest.TestCase):
    def test_benchmark_run_requires_runtime_booleans(self) -> None:
        task = BenchmarkTask("task-1", "game-d", "collect wood", 300, 1, "adapter")
        run = FakeBenchmarkEnvironment().run(task, 0)

        with self.assertRaisesRegex(ContractViolation, "must be booleans"):
            replace(run, success=cast(bool, 1))

    def test_cross_game_benchmark_aggregates_required_metrics(self) -> None:
        benchmark_config = (
            Path(__file__).resolve().parents[2] / "configs" / "benchmarks" / "uga-bench-smoke.yaml"
        )
        loaded = load_benchmark_tasks(benchmark_config)
        self.assertEqual(loaded[0].repeat, 3)
        task = BenchmarkTask("task-1", "game-d", "collect wood", 300, 2, "adapter")
        report = BenchmarkRunner().run((task,), {"game-d": FakeBenchmarkEnvironment()})
        self.assertEqual(report.success_rate, 1.0)
        self.assertEqual(report.games, ("game-d",))
        self.assertEqual(dict(report.game_success_rates)["game-d"], 1.0)
        self.assertEqual(dict(report.split_success_rates)["test"], 1.0)
        self.assertEqual(report.agent_human_time_ratio, 1.25)
        self.assertEqual(report.capture_latency.p99, 3.0)

    def test_owned_fixture_benchmark_covers_three_train_games_and_heldout_game(self) -> None:
        root = Path(__file__).resolve().parents[2]
        tasks = load_benchmark_tasks(root / "configs" / "benchmarks" / "uga-bench-fixture.yaml")
        environments = fixture_environments()

        report = BenchmarkRunner().run(tasks, environments)

        self.assertEqual(report.runs, 20)
        self.assertEqual(len(report.games), 4)
        self.assertEqual(dict(report.split_success_rates), {"test": 1.0, "train": 1.0})
        self.assertEqual(report.success_rate, 1.0)

    def test_fixture_benchmark_can_execute_a_trained_decoder_checkpoint(self) -> None:
        root = Path(__file__).resolve().parents[2]
        tasks = load_benchmark_tasks(root / "configs" / "benchmarks" / "uga-bench-fixture.yaml")
        checkpoint = DecoderCheckpoint(
            "fixture-policy",
            8,
            (
                (10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                (0.0, 10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                (0.0,) * 8,
                (0.0,) * 8,
            ),
            (0.0, 0.0, 0.0, 0.0),
            8,
            1.0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            checkpoint_path = checkpoint.save(artifact_root / "checkpoint.json")
            dataset = artifact_root / "dataset.json"
            config = artifact_root / "config.yaml"
            samples = artifact_root / "samples.jsonl"
            for path in (dataset, config, samples):
                path.write_text(path.name, encoding="utf-8")
            artifact = TrainingArtifactManifest(
                "fixture-policy",
                checkpoint_path.name,
                "1.1",
                str(dataset),
                "test-revision",
                str(config),
                str(samples),
                "fixture",
                sha256_file(checkpoint_path),
                sha256_file(dataset),
                sha256_file(config),
                sha256_file(samples),
                (("move_mse", 0.0),),
                (("base_model", "fixture-only"),),
            )
            artifact_path = artifact.write(artifact_root / "training-artifact.json")
            environments, artifact_digest = verified_fixture_environments(artifact_path)

            runs = tuple(
                environments[task.game_id].run(task, repetition)
                for task in tasks
                for repetition in range(task.repeat)
            )
            report = BenchmarkRunner().summarize(
                runs,
                tasks,
                expected_policy_artifact_sha256=artifact_digest,
            )

            mismatched = replace(artifact, artifact_id="claimed-policy")
            mismatched_path = mismatched.write(artifact_root / "mismatched-training-artifact.json")
            with self.assertRaisesRegex(ContractViolation, "identity does not match"):
                verified_fixture_environments(mismatched_path)

        self.assertEqual(report.success_rate, 1.0)
        self.assertEqual(dict(report.split_success_rates)["test"], 1.0)
        self.assertEqual(report.policy_artifact_sha256, artifact_digest)

    def test_benchmark_jsonl_loads_and_writes_self_describing_report(self) -> None:
        task = BenchmarkTask(
            "task-1",
            "game-d",
            "collect wood",
            300,
            1,
            "adapter",
            BenchmarkSplit.TEST,
        )
        run = FakeBenchmarkEnvironment().run(task, 0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runs = root / "runs.jsonl"
            write_benchmark_runs((run,), runs)
            loaded = load_benchmark_runs(runs)
            report = BenchmarkRunner().summarize(loaded, (task,))
            output = write_benchmark_report(report, root / "report.json")
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["runs"], 1)
            self.assertEqual(len(report.task_plan_sha256), 64)

            payload = json.loads(runs.read_text(encoding="utf-8"))
            payload["success"] = "false"
            runs.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractViolation, "JSON boolean"):
                load_benchmark_runs(runs)

    def test_benchmark_summary_rejects_incomplete_or_duplicate_cohort(self) -> None:
        task = BenchmarkTask("task-1", "game-d", "collect wood", 300, 2, "adapter")
        run = FakeBenchmarkEnvironment().run(task, 0)

        with self.assertRaisesRegex(ContractViolation, "expected task cohort"):
            BenchmarkRunner().summarize((run,), (task,))
        with self.assertRaisesRegex(ContractViolation, "duplicate run identities"):
            BenchmarkRunner().summarize((run, run), (task,))

    def test_dashboard_renders_state_and_routes_operator_commands(self) -> None:
        state = DashboardState(
            "frame-1",
            "data:image/png;base64,AA==",
            "goal",
            "subgoal",
            ControlMode.PLAY_3D,
            None,
            "skill",
            "action",
            0.9,
            False,
            30.0,
            5.0,
            50.0,
            1024,
            1,
            2,
            None,
        )
        document = render_dashboard(state)
        self.assertIn('data-command="emergency_release"', document)
        control = FakeOperatorControl()
        DashboardCommandRouter(control).execute(DashboardCommand.EMERGENCY_RELEASE)
        self.assertEqual(control.commands, ["emergency_release"])

    def test_replay_debugger_contains_timeline_and_video(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = make_episode(root)
            output = write_replay_debugger(episode, episode / "replay.html")
            document = output.read_text(encoding="utf-8")
            self.assertIn("video.mp4", document)
            self.assertIn("action-1", document)
            self.assertIn('type="range"', document)
            self.assertIn('id="uga-replay-data"', document)
            self.assertIn("100_000_000", document)

    def test_training_and_release_manifests_are_self_describing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "model.json"
            model.write_text("{}", encoding="utf-8")
            dataset = root / "dataset.json"
            dataset.write_text("{}", encoding="utf-8")
            training_config = root / "training.yaml"
            training_config.write_text("stage: motor_bc\n", encoding="utf-8")
            samples = root / "samples.jsonl"
            samples.write_text("{}\n", encoding="utf-8")
            training = TrainingArtifactManifest(
                "artifact-1",
                "model.json",
                "1.1",
                str(dataset),
                "workspace-unversioned",
                str(training_config),
                str(samples),
                "qwen-fixture",
                sha256_file(model),
                sha256_file(dataset),
                sha256_file(training_config),
                sha256_file(samples),
                (("movement_mse", 0.1),),
                (("model", "test-only"),),
            )
            artifact_path = training.write(root / "training-artifact.json")
            self.assertTrue(artifact_path.is_file())
            TrainingArtifactManifest.load(artifact_path).verify(root)
            model.write_text('{"tampered":true}', encoding="utf-8")
            with self.assertRaisesRegex(ContractViolation, "digest mismatch"):
                TrainingArtifactManifest.load(artifact_path).verify(root)
            manifest = ReleaseManifest(
                "0.1.0-dev",
                "1.1",
                "workspace-unversioned",
                hash_artifacts(root, ("model.json", "training-artifact.json")),
                (
                    ReleaseGate("automated-tests", GateStatus.PASSED, "local suite"),
                    ReleaseGate("gameplay-soak", GateStatus.NOT_RUN, "operator required"),
                ),
            )
            self.assertFalse(manifest.releasable)
            written = manifest.write(root / "release-manifest.json")
            self.assertIn('"releasable": false', written.read_text(encoding="utf-8"))

    def test_development_bundle_cannot_claim_release_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "native").mkdir()
            (root / "package.whl").write_bytes(b"wheel")
            (root / "package.tar.gz").write_bytes(b"source")
            (root / "native" / "uga_capture.dll").write_bytes(b"native")
            (root / "third-party-inventory.json").write_text("{}", encoding="utf-8")
            (root / "run_uga.ps1").write_text("Write-Output uga", encoding="utf-8")
            manifest = build_development_manifest(
                root,
                source_revision="test-revision",
                package_smoke_passed=True,
            )
            self.assertFalse(manifest.releasable)
            self.assertEqual(
                dict(manifest.artifacts).keys(),
                {
                    "package.whl",
                    "package.tar.gz",
                    "native/uga_capture.dll",
                    "run_uga.ps1",
                    "third-party-inventory.json",
                },
            )
            self.assertEqual(manifest.gates[2].status, GateStatus.PASSED)
            manifest.write(root / "release-manifest.json")
            loaded = ReleaseManifest.load(root / "release-manifest.json")
            loaded.verify(root)
            (root / "unexpected.txt").write_text("untrusted", encoding="utf-8")
            with self.assertRaisesRegex(ContractViolation, "file set mismatch"):
                loaded.verify(root)
            (root / "unexpected.txt").unlink()
            (root / "run_uga.ps1").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ContractViolation, "digest mismatch"):
                loaded.verify(root)

    def test_qualification_ledger_hashes_and_revalidates_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "automated-tests.txt"
            evidence.write_text("70 tests passed", encoding="utf-8")
            ledger = QualificationLedger.initialize("revision-1").with_record(
                QualificationRecord(
                    "automated-tests",
                    GateStatus.PASSED,
                    "local suite",
                    hash_evidence(root, ("automated-tests.txt",)),
                )
            )
            path = ledger.write(root / "qualification.json")
            loaded = QualificationLedger.load(path)
            loaded.verify_artifacts(root)
            self.assertFalse(loaded.releasable)
            evidence.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ContractViolation, "digest mismatch"):
                loaded.verify_artifacts(root)

    def test_license_gate_requires_license_artifact(self) -> None:
        with self.assertRaisesRegex(ContractViolation, "LICENSE"):
            QualificationRecord(
                "repository-license",
                GateStatus.PASSED,
                "owner selected a license",
                (EvidenceArtifact("evidence/license.txt", "0" * 64),),
            )

    def test_partial_all_passed_manifest_is_not_releasable(self) -> None:
        manifest = ReleaseManifest(
            "1.0.0",
            "1.1",
            "revision-1",
            (("package.whl", "0" * 64),),
            (ReleaseGate("automated-tests", GateStatus.PASSED, "suite passed"),),
        )
        self.assertFalse(manifest.releasable)


if __name__ == "__main__":
    unittest.main()
