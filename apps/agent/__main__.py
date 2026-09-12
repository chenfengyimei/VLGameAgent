from __future__ import annotations

import argparse
import asyncio
import json
import sys

from uga.core.errors import ContractViolation
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


def _run_safely(args: argparse.Namespace) -> int:
    try:
        return _run(args)
    except ContractViolation as error:
        print(f"uga-agent: {error}", file=sys.stderr)
        return 2


def _client_fraction(value: str) -> float:
    fraction = float(value)
    if not 0.0 <= fraction <= 1.0:
        raise argparse.ArgumentTypeError("client fraction must be within [0, 1]")
    return fraction


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
    run_parser.add_argument(
        "--policy",
        choices=["scripted", "vlm"],
        default="scripted",
        help="decision source: a scripted tap timeline or a vision-language planner",
    )
    run_parser.add_argument(
        "--duration-seconds",
        type=float,
        default=30.0,
        help="0 = run until stopped (Ctrl+C or the Ctrl+Shift+F12 emergency hotkey)",
    )
    run_parser.add_argument(
        "--tap-delay", type=float, default=2.0, help="seconds before the scripted tap"
    )
    run_parser.add_argument(
        "--tap-interval-seconds",
        type=float,
        default=0.0,
        help="repeat the tap timeline every N seconds (0 = tap once)",
    )
    run_parser.add_argument(
        "--tap-x-fraction",
        type=_client_fraction,
        default=0.5,
        help="tap point as a horizontal fraction of the client area",
    )
    run_parser.add_argument(
        "--tap-y-fraction",
        type=_client_fraction,
        default=0.79,
        help="tap point as a vertical fraction of the client area",
    )
    run_parser.add_argument("--observation-hz", type=float, default=2.0)
    run_parser.add_argument("--record", help="optional episode recording root directory")
    run_parser.add_argument(
        "--vlm-base-url",
        default="http://127.0.0.1:1234/v1",
        help="OpenAI-compatible vision endpoint (LM Studio or any cloud vision API)",
    )
    run_parser.add_argument(
        "--vlm-model",
        default="qwen3-vl-4b-instruct",
        help="vision model name exposed at the endpoint",
    )
    run_parser.add_argument(
        "--vlm-api-key-env",
        default="UGA_VLM_API_KEY",
        help="environment variable that holds the vision API key (empty for local servers)",
    )
    run_parser.add_argument(
        "--vlm-decision-interval",
        type=float,
        default=6.0,
        help="seconds between vision planner decisions",
    )
    run_parser.add_argument(
        "--vlm-timeout-seconds",
        type=float,
        default=30.0,
        help="vision request timeout",
    )
    run_parser.add_argument(
        "--vlm-no-thinking",
        action="store_true",
        help="ask thinking-style models (GLM-4.xV) to answer without a reasoning pass",
    )
    run_parser.add_argument(
        "--vlm-extra-body",
        help="JSON object merged into the vision request body (e.g. "
        '\'{"enable_thinking": false}\' for DashScope Qwen3 models)',
    )
    run_parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8787,
        help="port for the live decision dashboard at http://127.0.0.1:<port> (0 = disabled)",
    )
    run_parser.add_argument(
        "--vision-mode", choices=["auto", "local", "hybrid"], default="auto"
    )
    run_parser.add_argument("--ocr", choices=["auto", "off"], default="auto")
    run_parser.add_argument("--max-recoveries", type=int, default=2)
    run_parser.add_argument("--verifier-base-url")
    run_parser.add_argument("--verifier-model")
    run_parser.add_argument("--verifier-api-key-env", default="UGA_VERIFIER_API_KEY")
    args = parser.parse_args()
    if args.command is None or args.command == "smoke":
        _smoke()
        return
    if args.command == "run":
        raise SystemExit(_run_safely(args))
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    cli()
