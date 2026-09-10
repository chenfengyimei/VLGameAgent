from __future__ import annotations

import argparse
import asyncio
import json

from uga.core.runtime import AgentRuntime


async def main() -> None:
    """Run a deterministic lifecycle smoke test without emitting any input."""
    runtime = AgentRuntime()
    await runtime.start()
    await runtime.pause()
    await runtime.resume()
    await runtime.stop()
    await runtime.shutdown()
    print(json.dumps(runtime.status(), sort_keys=True))


def _smoke() -> None:
    """Synchronous console-script entry point for the lifecycle smoke."""
    asyncio.run(main())


def _run(args: argparse.Namespace) -> int:
    from apps.agent import run as run_module

    return int(run_module.main(args))


def cli() -> None:
    """Synchronous console-script entry point."""
    parser = argparse.ArgumentParser(description="UGA agent runtime")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("smoke", help="run the deterministic lifecycle smoke test")
    run_parser = subparsers.add_parser(
        "run",
        help="run the realtime agent loop against a live window from a game profile",
    )
    run_parser.add_argument("--profile", required=True, help="game profile YAML path")
    run_parser.add_argument("--goal", default="Interact with the target")
    run_parser.add_argument("--duration-seconds", type=float, default=30.0)
    run_parser.add_argument(
        "--tap-delay", type=float, default=2.0, help="seconds before the scripted tap"
    )
    run_parser.add_argument("--observation-hz", type=float, default=2.0)
    run_parser.add_argument("--record", help="optional episode recording root directory")
    args = parser.parse_args()
    if args.command is None or args.command == "smoke":
        _smoke()
        return
    if args.command == "run":
        raise SystemExit(_run(args))
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    cli()
