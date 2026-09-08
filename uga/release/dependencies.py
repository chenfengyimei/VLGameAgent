from __future__ import annotations

import importlib.metadata
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from uga.core.errors import ContractViolation

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


def runtime_requirement_names(pyproject: str | Path) -> tuple[str, ...]:
    with Path(pyproject).open("rb") as stream:
        payload = tomllib.load(stream)
    project = payload.get("project")
    dependencies = project.get("dependencies") if isinstance(project, dict) else None
    if not isinstance(dependencies, list):
        raise ContractViolation("pyproject runtime dependencies are invalid")
    names: list[str] = []
    for requirement in dependencies:
        match = _REQUIREMENT_NAME.match(str(requirement))
        if match is None:
            raise ContractViolation(f"invalid runtime dependency: {requirement}")
        names.append(match.group(1))
    return tuple(names)


def python_dependency_records(names: tuple[str, ...]) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    for name in names:
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ContractViolation(f"runtime dependency is not installed: {name}") from exc
        metadata = distribution.metadata
        license_value = _metadata_field(metadata, "License-Expression")
        license_value = license_value or _metadata_field(metadata, "License") or "UNKNOWN"
        source = _metadata_field(metadata, "Home-page") or _project_url(
            metadata.get_all("Project-URL") or []
        )
        records.append(
            {
                "name": _metadata_field(metadata, "Name") or name,
                "version": distribution.version,
                "source": source or "UNKNOWN",
                "license_declared": license_value,
                "review_required": license_value == "UNKNOWN",
            }
        )
    return tuple(sorted(records, key=lambda item: str(item["name"]).casefold()))


def cargo_dependency_records(cargo_manifest: str | Path) -> tuple[dict[str, object], ...]:
    command = (
        "cargo",
        "metadata",
        "--format-version",
        "1",
        "--locked",
        "--manifest-path",
        str(Path(cargo_manifest).resolve()),
    )
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise ContractViolation(f"cargo metadata failed: {completed.stderr.strip()}")
    try:
        payload: Any = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ContractViolation("cargo metadata returned invalid JSON") from exc
    return parse_cargo_metadata(payload)


def parse_cargo_metadata(payload: object) -> tuple[dict[str, object], ...]:
    packages = payload.get("packages") if isinstance(payload, dict) else None
    if not isinstance(packages, list):
        raise ContractViolation("cargo metadata packages are invalid")
    records: list[dict[str, object]] = []
    for package in packages:
        if not isinstance(package, dict) or package.get("source") is None:
            continue
        license_value = str(package.get("license") or "UNKNOWN")
        repository = package.get("repository")
        records.append(
            {
                "name": str(package["name"]),
                "version": str(package["version"]),
                "source": str(repository or package["source"]),
                "license_declared": license_value,
                "review_required": license_value == "UNKNOWN",
            }
        )
    return tuple(
        sorted(records, key=lambda item: (str(item["name"]).casefold(), str(item["version"])))
    )


def npm_dependency_records(package_lock: str | Path) -> tuple[dict[str, object], ...]:
    try:
        payload: Any = json.loads(Path(package_lock).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractViolation(f"npm lockfile is invalid: {exc}") from exc
    return parse_npm_lock(payload)


def parse_npm_lock(payload: object) -> tuple[dict[str, object], ...]:
    packages = payload.get("packages") if isinstance(payload, dict) else None
    if not isinstance(packages, dict):
        raise ContractViolation("npm lockfile packages are invalid")
    records: list[dict[str, object]] = []
    for package_path, package in packages.items():
        if not package_path or not isinstance(package_path, str):
            continue
        if not isinstance(package, dict):
            raise ContractViolation("npm lockfile package entry is invalid")
        marker = "node_modules/"
        if marker not in package_path:
            continue
        name = package_path.rsplit(marker, 1)[1]
        license_value = str(package.get("license") or "UNKNOWN")
        source = str(package.get("resolved") or "UNKNOWN")
        records.append(
            {
                "name": name,
                "version": str(package.get("version") or "UNKNOWN"),
                "source": source,
                "license_declared": license_value,
                "scope": "build",
                "review_required": license_value == "UNKNOWN" or source == "UNKNOWN",
            }
        )
    return tuple(
        sorted(records, key=lambda item: (str(item["name"]).casefold(), str(item["version"])))
    )


def build_dependency_inventory(
    pyproject: str | Path,
    cargo_manifest: str | Path,
    npm_lock: str | Path | None = None,
) -> dict[str, object]:
    python = python_dependency_records(runtime_requirement_names(pyproject))
    cargo = cargo_dependency_records(cargo_manifest)
    npm = () if npm_lock is None else npm_dependency_records(npm_lock)
    unknown = tuple(
        f"{ecosystem}:{record['name']}@{record['version']}"
        for ecosystem, records in (("python", python), ("cargo", cargo), ("npm", npm))
        for record in records
        if record["review_required"]
    )
    return {
        "schema": "uga.dependency_inventory",
        "schema_version": "1.1",
        "python": python,
        "cargo": cargo,
        "npm": npm,
        "unknown_license_declarations": unknown,
        "license_declarations_complete": not unknown,
        "legal_review_complete": False,
    }


def write_dependency_inventory(payload: dict[str, object], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return destination


def _project_url(values: list[str]) -> str | None:
    parsed: list[tuple[str, str]] = []
    for value in values:
        label, separator, url = value.partition(",")
        if separator and url.strip():
            parsed.append((label.strip().casefold(), url.strip()))
    preferred = ("source", "repository", "homepage", "source code")
    return next(
        (url for label, url in parsed if label in preferred),
        parsed[0][1] if parsed else None,
    )


def _metadata_field(metadata: importlib.metadata.PackageMetadata, key: str) -> str | None:
    values = metadata.get_all(key)
    return values[0] if values else None
