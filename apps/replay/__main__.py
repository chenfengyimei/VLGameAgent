from __future__ import annotations

import argparse
from pathlib import Path

from uga.recording.debugger import write_replay_debugger


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the UGA replay debugger UI")
    parser.add_argument("episode", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.episode / "replay.html"
    print(write_replay_debugger(args.episode, output))


if __name__ == "__main__":
    main()
