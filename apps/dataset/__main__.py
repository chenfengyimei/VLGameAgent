from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from uga.dataset.manifest import DatasetManifest
from uga.dataset.opencua import (
    OpenCuaExporter,
    load_opencua_trajectory,
    write_opencua_trajectory,
)
from uga.dataset.processor import DatasetProcessor
from uga.dataset.validator import DatasetValidator
from uga.dataset.viewer import write_episode_viewer


def _validate(args: argparse.Namespace) -> None:
    report = DatasetValidator(
        max_capture_gap_ns=args.max_capture_gap_ns,
        max_input_gap_ns=args.max_input_gap_ns,
        max_mouse_delta=args.max_mouse_delta,
    ).validate(args.episode)
    document = json.dumps(asdict(report), indent=2)
    if args.output:
        args.output.write_text(document + "\n", encoding="utf-8")
        print(args.output.resolve())
    else:
        print(document)


def _process(args: argparse.Namespace) -> None:
    episode = DatasetProcessor(max_alignment_delay_ns=args.max_alignment_delay_ns).process(
        args.episode
    )
    payload = {
        "episode_id": episode.episode_id,
        "game_id": episode.game_id,
        "duration_ns": episode.duration_ns,
        "samples": [asdict(sample) for sample in episode.samples],
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(args.output.resolve())


def _view(args: argparse.Namespace) -> None:
    episode = DatasetProcessor(max_alignment_delay_ns=args.max_alignment_delay_ns).process(
        args.episode
    )
    print(write_episode_viewer(episode, args.output).resolve())


def _manifest(args: argparse.Namespace) -> None:
    manifest = DatasetManifest.load(args.manifest)
    manifest.verify_episode_artifacts(args.root)
    print(
        json.dumps(
            {
                "dataset_id": manifest.dataset_id,
                "dataset_version": manifest.dataset_version,
                "source_revision": manifest.source_revision,
                "episodes": len(manifest.episodes),
                "hours": manifest.hours(),
                "locked_test_games": manifest.locked_test_games,
                "distribution": {
                    category.value: fraction
                    for category, fraction in manifest.category_distribution()
                },
            },
            indent=2,
        )
    )


def _opencua_export(args: argparse.Namespace) -> None:
    trajectory = OpenCuaExporter().export(args.episode)
    print(write_opencua_trajectory(trajectory, args.output).resolve())


def _opencua_import(args: argparse.Namespace) -> None:
    trajectory = load_opencua_trajectory(args.trajectory)
    print(
        json.dumps(
            {
                "task_id": trajectory.task_id,
                "steps": len(trajectory.steps),
                "actions": sum(len(step.ground_truth_actions) for step in trajectory.steps),
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and inspect UGA Episode datasets")
    subparsers = parser.add_subparsers(required=True)

    validate = subparsers.add_parser("validate", help="run Episode quality gates")
    validate.add_argument("episode", type=Path)
    validate.add_argument("--output", type=Path)
    validate.add_argument("--max-capture-gap-ns", type=int, default=500_000_000)
    validate.add_argument("--max-input-gap-ns", type=int, default=500_000_000)
    validate.add_argument("--max-mouse-delta", type=int, default=5000)
    validate.set_defaults(handler=_validate)

    process = subparsers.add_parser("process", help="write timestamp-aligned samples")
    process.add_argument("episode", type=Path)
    process.add_argument("--output", type=Path, required=True)
    process.add_argument("--max-alignment-delay-ns", type=int, default=1_000_000_000)
    process.set_defaults(handler=_process)

    view = subparsers.add_parser("view", help="render the Episode dataset viewer")
    view.add_argument("episode", type=Path)
    view.add_argument("--output", type=Path, required=True)
    view.add_argument("--max-alignment-delay-ns", type=int, default=1_000_000_000)
    view.set_defaults(handler=_view)

    manifest = subparsers.add_parser("manifest", help="verify and summarize a dataset manifest")
    manifest.add_argument("manifest", type=Path)
    manifest.add_argument("--root", type=Path, required=True)
    manifest.set_defaults(handler=_manifest)

    export = subparsers.add_parser("opencua-export", help="export GUI Episode to OpenCUA")
    export.add_argument("episode", type=Path)
    export.add_argument("--output", type=Path, required=True)
    export.set_defaults(handler=_opencua_export)

    import_trajectory = subparsers.add_parser(
        "opencua-import", help="validate and summarize OpenCUA trajectory"
    )
    import_trajectory.add_argument("trajectory", type=Path)
    import_trajectory.set_defaults(handler=_opencua_import)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
