from __future__ import annotations

import argparse
import os
from pathlib import Path

from uga.release.development import build_development_manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write a truthful UGA development release manifest"
    )
    parser.add_argument("bundle", type=Path)
    parser.add_argument(
        "--source-revision",
        default=os.environ.get("UGA_SOURCE_REVISION", "workspace-unversioned"),
    )
    parser.add_argument("--package-smoke-passed", action="store_true")
    args = parser.parse_args()
    manifest = build_development_manifest(
        args.bundle,
        source_revision=args.source_revision,
        package_smoke_passed=args.package_smoke_passed,
    )
    print(manifest.write(args.bundle / "release-manifest.json"))


if __name__ == "__main__":
    main()
