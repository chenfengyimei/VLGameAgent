from __future__ import annotations

import argparse
import json
from pathlib import Path

from uga.core.errors import ContractViolation
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
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
