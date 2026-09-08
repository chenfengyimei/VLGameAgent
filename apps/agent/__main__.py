from __future__ import annotations

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


def cli() -> None:
    """Synchronous console-script entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
