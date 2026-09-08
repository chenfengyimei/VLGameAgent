from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from uga.benchmark.io import load_benchmark_runs, write_benchmark_report
from uga.benchmark.runner import BenchmarkRunner
from uga.benchmark.schema import load_benchmark_tasks


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
    report = BenchmarkRunner().summarize(load_benchmark_runs(args.runs))
    if args.output:
        print(write_benchmark_report(report, args.output).resolve())
    else:
        print(json.dumps(asdict(report), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and summarize UGA-Bench runs")
    subparsers = parser.add_subparsers(required=True)

    validate = subparsers.add_parser("validate-config", help="validate benchmark task YAML")
    validate.add_argument("config", type=Path)
    validate.set_defaults(handler=_validate_config)

    summarize = subparsers.add_parser("summarize", help="aggregate benchmark JSONL runs")
    summarize.add_argument("runs", type=Path)
    summarize.add_argument("--output", type=Path)
    summarize.set_defaults(handler=_summarize)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
