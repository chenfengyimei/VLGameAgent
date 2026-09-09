from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from uga.environment.fixture_world import FixtureScenario, FixtureWorld
from uga.release.fixture_corpus import run_fixture_corpus
from uga.release.fixture_qualification import run_fixture_qualification
from uga.release.manifest import GateStatus
from uga.release.preflight import build_qualification_preflight
from uga.release.qualification import (
    REQUIRED_GATE_IDS,
    QualificationLedger,
    QualificationRecord,
    build_qualified_release_manifest,
    hash_evidence,
)

_LEDGER_NAME = "qualification.json"


def _ledger_path(root: Path) -> Path:
    return root.resolve() / _LEDGER_NAME


def _init(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = _ledger_path(root)
    if path.exists():
        raise FileExistsError(path)
    print(QualificationLedger.initialize(args.source_revision).write(path))


def _record(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    path = _ledger_path(root)
    ledger = QualificationLedger.load(path)
    artifacts = hash_evidence(root, tuple(args.artifact))
    updated = ledger.with_record(
        QualificationRecord(
            args.gate,
            GateStatus(args.status),
            args.evidence,
            artifacts,
        )
    )
    updated.verify_artifacts(root)
    print(updated.write(path))


def _status(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    ledger = QualificationLedger.load(_ledger_path(root))
    ledger.verify_artifacts(root)
    print(
        json.dumps(
            {
                "source_revision": ledger.source_revision,
                "releasable": ledger.releasable,
                "gates": {record.gate_id: record.status.value for record in ledger.records},
            },
            indent=2,
        )
    )


def _manifest(args: argparse.Namespace) -> None:
    evidence_root = args.root.resolve()
    bundle = args.bundle.resolve()
    ledger = QualificationLedger.load(_ledger_path(evidence_root))
    manifest = build_qualified_release_manifest(
        bundle,
        ledger,
        evidence_root,
        version=args.version,
        preflight_path=args.preflight,
        project_root=args.project_root,
    )
    output = args.output.resolve() if args.output else bundle / "release-manifest.json"
    print(manifest.write(output))


def _preflight(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    ledger = QualificationLedger.load(_ledger_path(root))
    ledger.verify_artifacts(root)
    report = build_qualification_preflight(
        ledger,
        args.project_root,
        training_artifact_path=args.training_artifact,
        dataset_manifest_path=args.dataset_manifest,
        dataset_root=args.dataset_root,
    )
    output = args.output.resolve() if args.output else root / "preflight.json"
    print(report.write(output))


def _fixture(args: argparse.Namespace) -> None:
    scenario = FixtureScenario(args.scenario)
    title = args.title or f"^{re.escape(FixtureWorld(scenario=scenario).window_title)}$"
    print(
        run_fixture_qualification(
            title_pattern=title,
            duration_seconds=args.duration_seconds,
            target_fps=args.target_fps,
            backend_preference=args.backend,
            episode_root=args.episode_root,
            report_path=args.output,
            allow_physical_input=args.allow_physical_input,
            exercise_focus_loss=args.exercise_focus_loss,
            exercise_emergency_hotkey=args.exercise_emergency_hotkey,
            fixture_scenario=scenario.value,
            expected_pid=args.expected_pid,
        )
    )


def _corpus(args: argparse.Namespace) -> None:
    print(
        run_fixture_corpus(
            project_root=args.project_root,
            output_root=args.output_root,
            train_duration_seconds=args.train_duration_seconds,
            test_duration_seconds=args.test_duration_seconds,
            target_fps=args.target_fps,
            backend=args.backend,
            allow_physical_input=args.allow_physical_input,
            require_release_volume=not args.development_smoke,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage UGA V1 qualification evidence")
    subparsers = parser.add_subparsers(required=True)

    initialize = subparsers.add_parser("init", help="create an empty evidence ledger")
    initialize.add_argument("root", type=Path)
    initialize.add_argument("--source-revision", required=True)
    initialize.set_defaults(handler=_init)

    record = subparsers.add_parser("record", help="record one gate result")
    record.add_argument("root", type=Path)
    record.add_argument("gate", choices=REQUIRED_GATE_IDS)
    record.add_argument("status", choices=tuple(status.value for status in GateStatus))
    record.add_argument("--evidence", required=True)
    record.add_argument("--artifact", action="append", default=[])
    record.set_defaults(handler=_record)

    status = subparsers.add_parser("status", help="verify and show current evidence")
    status.add_argument("root", type=Path)
    status.set_defaults(handler=_status)

    manifest = subparsers.add_parser("manifest", help="build a manifest from verified evidence")
    manifest.add_argument("root", type=Path)
    manifest.add_argument("bundle", type=Path)
    manifest.add_argument("--version", required=True)
    manifest.add_argument("--preflight", type=Path, required=True)
    manifest.add_argument("--project-root", type=Path, default=Path("."))
    manifest.add_argument("--output", type=Path)
    manifest.set_defaults(handler=_manifest)

    preflight = subparsers.add_parser(
        "preflight", help="inspect host and evidence prerequisites without changing them"
    )
    preflight.add_argument("root", type=Path)
    preflight.add_argument("--project-root", type=Path, default=Path("."))
    preflight.add_argument("--training-artifact", type=Path)
    preflight.add_argument("--dataset-manifest", type=Path)
    preflight.add_argument("--dataset-root", type=Path)
    preflight.add_argument("--output", type=Path)
    preflight.set_defaults(handler=_preflight)

    fixture = subparsers.add_parser(
        "fixture", help="run supervised capture, physical-input, Recorder, and Replay checks"
    )
    fixture.add_argument("--title")
    fixture.add_argument(
        "--expected-pid",
        type=int,
        required=True,
        help="trusted PID of the already-running developer-owned fixture",
    )
    fixture.add_argument(
        "--scenario",
        choices=tuple(item.value for item in FixtureScenario),
        default=FixtureScenario.EXPLORATION.value,
    )
    fixture.add_argument("--duration-seconds", type=float, default=8.0)
    fixture.add_argument("--target-fps", type=float, default=30.0)
    fixture.add_argument(
        "--backend",
        choices=("auto", "windows_graphics_capture", "dxgi_duplication", "gdi_fallback"),
        default="gdi_fallback",
    )
    fixture.add_argument("--episode-root", type=Path, required=True)
    fixture.add_argument("--output", type=Path, required=True)
    fixture.add_argument("--allow-physical-input", action="store_true")
    fixture.add_argument("--exercise-focus-loss", action="store_true")
    fixture.add_argument("--exercise-emergency-hotkey", action="store_true")
    fixture.set_defaults(handler=_fixture)

    corpus = subparsers.add_parser(
        "corpus", help="collect the owned Train A/B/C and held-out Fixture corpus"
    )
    corpus.add_argument("--project-root", type=Path, default=Path("."))
    corpus.add_argument("--output-root", type=Path, required=True)
    corpus.add_argument("--train-duration-seconds", type=float, default=6000.0)
    corpus.add_argument("--test-duration-seconds", type=float, default=600.0)
    corpus.add_argument("--target-fps", type=float, default=3.0)
    corpus.add_argument(
        "--backend",
        choices=("auto", "windows_graphics_capture", "dxgi_duplication", "gdi_fallback"),
        default="gdi_fallback",
    )
    corpus.add_argument("--allow-physical-input", action="store_true")
    corpus.add_argument("--development-smoke", action="store_true")
    corpus.set_defaults(handler=_corpus)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
