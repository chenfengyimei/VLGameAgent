from __future__ import annotations

import argparse
import json
from pathlib import Path

from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.release.model_qualification import (
    MODEL_STAGES,
    build_model_qualification_report,
    build_model_stage_report,
)
from uga.release.revision import require_clean_source_revision
from uga.training.artifact import TrainingArtifactManifest
from uga.training.motor_pipeline import export_motor_samples, train_motor_policy


def _motor(args: argparse.Namespace) -> None:
    revision = require_clean_source_revision(args.project_root)
    if args.source_revision != revision:
        raise ContractViolation("training source revision does not match clean Git HEAD")
    result = train_motor_policy(
        samples_path=args.samples,
        dataset_manifest_path=args.dataset_manifest,
        dataset_root=args.dataset_root,
        training_config_path=args.config,
        output_directory=args.output,
        policy_version=args.policy_version,
        source_revision=args.source_revision,
        base_model_license=args.base_model_license,
    )
    print(
        json.dumps(
            {
                "output_directory": str(result.output_directory),
                "checkpoint": str(result.checkpoint),
                "metrics": str(result.metrics),
                "artifact_manifest": str(result.artifact_manifest),
            },
            indent=2,
        )
    )


def _verify(args: argparse.Namespace) -> None:
    artifact_path = args.artifact.resolve()
    artifact = TrainingArtifactManifest.load(artifact_path)
    output = args.output.resolve() if args.output else artifact_path.parent
    artifact.verify(output)
    print(
        json.dumps(
            {
                "artifact": str(artifact_path),
                "artifact_id": artifact.artifact_id,
                "policy_version": artifact.artifact_id,
                "source_revision": artifact.source_revision,
                "verified": True,
            },
            indent=2,
        )
    )


def _prepare_motor(args: argparse.Namespace) -> None:
    print(export_motor_samples(tuple(args.episode), args.output))


def _stage_report(args: argparse.Namespace) -> None:
    print(
        build_model_stage_report(
            stage=args.stage,
            dataset_manifest_path=args.dataset_manifest,
            artifact_manifest_path=args.artifact,
            offline_metrics_path=args.offline_metrics,
            closed_loop_metrics_path=args.closed_loop_metrics,
            trainer_backend=args.trainer_backend,
            training_run_id=args.training_run_id,
            output_path=args.output,
            source_revision=require_clean_source_revision(args.project_root),
        )
    )


def _qualification_report(args: argparse.Namespace) -> None:
    print(
        build_model_qualification_report(
            dataset_manifest_path=args.dataset_manifest,
            stage_report_paths=tuple(args.stage_report),
            output_path=args.output,
            source_revision=require_clean_source_revision(args.project_root),
        )
    )


def _neural_motor(args: argparse.Namespace) -> None:
    from uga.training.neural_pipeline import train_neural_motor

    revision = require_clean_source_revision(args.project_root)
    artifact = train_neural_motor(
        train_path=args.train_samples,
        validation_path=args.validation_samples,
        dataset_manifest_path=args.dataset_manifest,
        dataset_root=args.dataset_root,
        config_path=args.config,
        output_directory=args.output,
        policy_version=args.policy_version,
        encoder_version=args.encoder_version,
        encoder_license=args.encoder_license,
        source_revision=revision,
    )
    print(
        json.dumps(
            {"artifact": str(artifact), "source_revision": revision, "release_qualified": False},
            indent=2,
        )
    )


def _neural_verify(args: argparse.Namespace) -> None:
    from uga.training.neural_pipeline import verify_neural_artifact

    record = verify_neural_artifact(args.artifact, expected_sha256=args.expected_sha256)
    print(
        json.dumps(
            {
                "artifact": str(args.artifact.resolve()),
                "policy_version": record["policy_version"],
                "integrity_verified": True,
                "externally_pinned": args.expected_sha256 is not None,
                "release_qualified": False,
            },
            indent=2,
        )
    )


def _neural_evaluate(args: argparse.Namespace) -> None:
    from uga.dataset.processor import DatasetSplit
    from uga.training.neural_pipeline import evaluate_neural_artifact

    print(
        evaluate_neural_artifact(
            artifact_path=args.artifact,
            samples_path=args.samples,
            dataset_root=args.dataset_root,
            split=DatasetSplit(args.split),
            output_path=args.output,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run UGA training stages")
    subparsers = parser.add_subparsers(required=True)
    prepare = subparsers.add_parser(
        "prepare-motor-samples",
        help="export canonical Episode actions as provenance-preserving motor samples",
    )
    prepare.add_argument("--episode", type=Path, action="append", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.set_defaults(handler=_prepare_motor)
    motor = subparsers.add_parser("motor", help="train the deterministic motor policy head")
    motor.add_argument("--samples", type=Path, required=True)
    motor.add_argument("--dataset-manifest", type=Path, required=True)
    motor.add_argument("--dataset-root", type=Path, required=True)
    motor.add_argument("--config", type=Path, default=Path("configs/training/motor_bc.yaml"))
    motor.add_argument("--output", type=Path, required=True)
    motor.add_argument("--policy-version", required=True)
    motor.add_argument("--source-revision", required=True)
    motor.add_argument("--project-root", type=Path, default=Path("."))
    motor.add_argument("--base-model-license", required=True)
    motor.set_defaults(handler=_motor)
    verify = subparsers.add_parser("verify", help="verify a training artifact and its inputs")
    verify.add_argument("artifact", type=Path)
    verify.add_argument("--output", type=Path)
    verify.set_defaults(handler=_verify)
    stage_report = subparsers.add_parser(
        "stage-report",
        help="bind one GPU training stage to typed offline and closed-loop metrics",
    )
    stage_report.add_argument("--stage", choices=MODEL_STAGES, required=True)
    stage_report.add_argument("--dataset-manifest", type=Path, required=True)
    stage_report.add_argument("--artifact", type=Path, required=True)
    stage_report.add_argument("--offline-metrics", type=Path, required=True)
    stage_report.add_argument("--closed-loop-metrics", type=Path, required=True)
    stage_report.add_argument("--trainer-backend", required=True)
    stage_report.add_argument("--training-run-id", required=True)
    stage_report.add_argument("--project-root", type=Path, default=Path("."))
    stage_report.add_argument("--output", type=Path, required=True)
    stage_report.set_defaults(handler=_stage_report)
    qualification = subparsers.add_parser(
        "qualification-report",
        help="combine exactly five verified model stage reports",
    )
    qualification.add_argument("--dataset-manifest", type=Path, required=True)
    qualification.add_argument("--stage-report", type=Path, action="append", required=True)
    qualification.add_argument("--project-root", type=Path, default=Path("."))
    qualification.add_argument("--output", type=Path, required=True)
    qualification.set_defaults(handler=_qualification_report)
    neural = subparsers.add_parser(
        "neural-motor", help="fit optional PyTorch motor BC, not V1 qualification"
    )
    neural.add_argument("--train-samples", type=Path, required=True)
    neural.add_argument("--validation-samples", type=Path, required=True)
    neural.add_argument("--dataset-manifest", type=Path, required=True)
    neural.add_argument("--dataset-root", type=Path, required=True)
    neural.add_argument("--config", type=Path, required=True)
    neural.add_argument("--output", type=Path, required=True)
    neural.add_argument("--policy-version", required=True)
    neural.add_argument("--encoder-version", required=True)
    neural.add_argument("--encoder-license", required=True)
    neural.add_argument("--project-root", type=Path, default=Path("."))
    neural.set_defaults(handler=_neural_motor)
    neural_verify = subparsers.add_parser(
        "neural-verify", help="verify numerical model and input snapshots"
    )
    neural_verify.add_argument("artifact", type=Path)
    neural_verify.add_argument("--expected-sha256")
    neural_verify.set_defaults(handler=_neural_verify)
    evaluate = subparsers.add_parser(
        "neural-evaluate", help="evaluate an explicit held-out split without fitting"
    )
    evaluate.add_argument("artifact", type=Path)
    evaluate.add_argument("--samples", type=Path, required=True)
    evaluate.add_argument("--dataset-root", type=Path, required=True)
    evaluate.add_argument("--split", choices=("validation", "test"), required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.set_defaults(handler=_neural_evaluate)
    args = parser.parse_args()
    try:
        args.handler(args)
    except (ContractViolation, BackendUnavailableError) as exc:
        parser.exit(2, f"uga-train: {exc}\n")


if __name__ == "__main__":
    main()
