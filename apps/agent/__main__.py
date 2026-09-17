from __future__ import annotations

import argparse
import asyncio
import json
import sys

from uga.core.errors import ContractViolation, FatalRuntimeError
from uga.core.runtime import AgentRuntime
from uga.policy.vision_transport import ProviderError


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


def _has_fatal_provider_error(error: BaseException) -> bool:
    if isinstance(error, FatalRuntimeError):
        return True
    if isinstance(error, ProviderError):
        return error.fatal
    if isinstance(error, BaseExceptionGroup):
        return any(_has_fatal_provider_error(child) for child in error.exceptions)
    return False


def _run_safely(args: argparse.Namespace) -> int:
    try:
        return _run(args)
    except ContractViolation as error:
        print(f"uga-agent: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        if not _has_fatal_provider_error(error):
            raise
        # TaskGroup wraps worker failures. Keep the non-retryable outcome
        # visible to the process supervisor without echoing provider bodies.
        print("uga-agent: non-retryable failure; manual intervention required", file=sys.stderr)
        return 78


def cli() -> None:
    """Console and direct entry share one parser, including every safety flag."""
    from apps.agent.run import build_parser

    parser = argparse.ArgumentParser(description="UGA agent runtime")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("smoke", help="run the deterministic lifecycle smoke test")
    subparsers.add_parser(
        "run", parents=[build_parser(add_help=False)],
        help="run the realtime agent against an explicitly selected window profile",
    )
    args = parser.parse_args()
    if args.command is None or args.command == "smoke":
        _smoke()
        return
    if args.command == "run":
        raise SystemExit(_run_safely(args))
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    cli()
