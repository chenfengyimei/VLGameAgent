from __future__ import annotations

import argparse
from pathlib import Path

from uga.core.errors import ContractViolation
from uga.release.dependencies import build_dependency_inventory, write_dependency_inventory


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate UGA dependency and license inventory")
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--cargo-manifest", type=Path, default=Path("native/Cargo.toml"))
    parser.add_argument("--npm-lock", type=Path, default=Path("package-lock.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-known", action="store_true")
    args = parser.parse_args()
    inventory = build_dependency_inventory(args.pyproject, args.cargo_manifest, args.npm_lock)
    if args.require_known and inventory["unknown_license_declarations"]:
        raise ContractViolation("dependency inventory contains unknown license declarations")
    print(write_dependency_inventory(inventory, args.output).resolve())


if __name__ == "__main__":
    main()
