from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from uga.benchmark.fixture import fixture_environments, verified_fixture_environments
from uga.benchmark.io import load_benchmark_runs, write_benchmark_report, write_benchmark_runs
from uga.benchmark.runner import BenchmarkRunner
from uga.benchmark.schema import load_benchmark_tasks
from uga.core.errors import ContractViolation
from uga.evaluation.grounding_qualification import build_grounding_qualification_report
from uga.evaluation.grounding_runner import (
    default_perception_builder,
    run_grounding_predictions,
)
from uga.policy.grounded_vlm import GroundedVlmPlanner
from uga.policy.vlm_planner import OpenAICompatibleVisionClient
from uga.release.revision import require_clean_source_revision


def _validate_config(args: argparse.Namespace) -> None:
    tasks = load_benchmark_tasks(args.config)
    print(
        json.dumps(
            {
                "tasks": len(tasks),
                "games": sorted({task.game_id for task in tasks}),
                "repetitions": sum(task.repeat for task in tasks),
            },
            indent=2,
        )
    )


def _summarize(args: argparse.Namespace) -> None:
    revision = (
        require_clean_source_revision(args.project_root)
        if args.output
        else "workspace-unversioned"
    )
    report = BenchmarkRunner(source_revision=revision).summarize(
        load_benchmark_runs(args.runs), load_benchmark_tasks(args.config)
    )
    if args.output:
        print(write_benchmark_report(report, args.output).resolve())
    else:
        print(json.dumps(asdict(report), indent=2))


def _fixture(args: argparse.Namespace) -> None:
    revision = require_clean_source_revision(args.project_root)
    tasks = load_benchmark_tasks(args.config)
    if args.artifact is None:
        environments = fixture_environments()
        artifact_digest = None
    else:
        environments, artifact_digest = verified_fixture_environments(args.artifact)
    runs = tuple(
        environments[task.game_id].run(task, repetition)
        for task in tasks
        for repetition in range(task.repeat)
    )
    output = write_benchmark_runs(runs, args.output)
    report = BenchmarkRunner(source_revision=revision).summarize(
        runs, tasks, expected_policy_artifact_sha256=artifact_digest
    )
    report_path = write_benchmark_report(report, args.report)
    print(json.dumps({"runs": str(output), "report": str(report_path)}, indent=2))


def _grounding_report(args: argparse.Namespace) -> None:
    report = build_grounding_qualification_report(
        annotations_path=args.annotations,
        predictions_path=args.predictions,
        output_path=args.output,
        source_revision=require_clean_source_revision(args.project_root),
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    print(report)
    if payload.get("passed") is not True:
        raise ContractViolation("grounding qualification thresholds did not pass")


def _grounding_run(args: argparse.Namespace) -> None:
    revision = require_clean_source_revision(args.project_root)
    client = OpenAICompatibleVisionClient(
        base_url=args.base_url,
        model=args.model,
        api_key=os.environ.get(args.api_key_env, ""),
        timeout_s=args.timeout_seconds,
        disable_thinking=args.no_thinking,
    )
    output = run_grounding_predictions(
        annotations_path=args.annotations,
        output_path=args.output,
        source_revision=revision,
        model_id=args.model,
        planner=GroundedVlmPlanner(client, structured_output=True),
        perception_builder=default_perception_builder(),
        progress=lambda current, total, sample: print(
            f"[{current}/{total}] {sample}", flush=True
        ),
    )
    print(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and summarize UGA-Bench runs")
    subparsers = parser.add_subparsers(required=True)

    validate = subparsers.add_parser("validate-config", help="validate benchmark task YAML")
    validate.add_argument("config", type=Path)
    validate.set_defaults(handler=_validate_config)

    summarize = subparsers.add_parser("summarize", help="aggregate benchmark JSONL runs")
    summarize.add_argument("runs", type=Path)
    summarize.add_argument("--config", type=Path, required=True)
    summarize.add_argument("--output", type=Path)
    summarize.add_argument("--project-root", type=Path, default=Path("."))
    summarize.set_defaults(handler=_summarize)

    fixture = subparsers.add_parser(
        "fixture", help="run the owned four-scenario closed-loop benchmark"
    )
    fixture.add_argument("--config", type=Path, required=True)
    fixture.add_argument("--output", type=Path, required=True)
    fixture.add_argument("--report", type=Path, required=True)
    fixture.add_argument("--project-root", type=Path, default=Path("."))
    policy = fixture.add_mutually_exclusive_group(required=True)
    policy.add_argument("--artifact", type=Path)
    policy.add_argument("--rule-baseline", action="store_true")
    fixture.set_defaults(handler=_fixture)

    grounding = subparsers.add_parser(
        "grounding-report",
        help="build a source-bound report for the 200-sample visual grounding corpus",
    )
    grounding.add_argument("--annotations", type=Path, required=True)
    grounding.add_argument("--predictions", type=Path, required=True)
    grounding.add_argument("--output", type=Path, required=True)
    grounding.add_argument("--project-root", type=Path, default=Path("."))
    grounding.set_defaults(handler=_grounding_report)

    grounding_run = subparsers.add_parser(
        "grounding-run",
        help="run a resumable local VLM + OCR pass over annotated grounding frames",
    )
    grounding_run.add_argument("--annotations", type=Path, required=True)
    grounding_run.add_argument("--output", type=Path, required=True)
    grounding_run.add_argument("--base-url", default="http://127.0.0.1:1234/v1")
    grounding_run.add_argument("--model", default="qwen3-vl-4b-instruct")
    grounding_run.add_argument("--api-key-env", default="UGA_VLM_API_KEY")
    grounding_run.add_argument("--timeout-seconds", type=float, default=60.0)
    grounding_run.add_argument("--no-thinking", action="store_true")
    grounding_run.add_argument("--project-root", type=Path, default=Path("."))
    grounding_run.set_defaults(handler=_grounding_run)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
