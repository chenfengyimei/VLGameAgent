from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    parse_json_text,
    read_text_limited,
)
from uga.core.errors import ContractViolation

REQUIRED_RELEASE_GATE_IDS = (
    "automated-tests",
    "package-build",
    "package-install-smoke",
    "capture-soak",
    "control-hardware",
    "recorder-10min",
    "dataset-5h",
    "model-training",
    "generalization-bench",
    "repository-license",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")


class GateStatus(StrEnum):
    PASSED = "passed"
    BLOCKED = "blocked"
    NOT_RUN = "not_run"


@dataclass(frozen=True, slots=True)
class ReleaseGate:
    gate_id: str
    status: GateStatus
    evidence: str

    def __post_init__(self) -> None:
        if not self.gate_id.strip() or not self.evidence.strip():
            raise ContractViolation("release gate requires id and evidence")


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    version: str
    schema_version: str
    source_revision: str
    artifacts: tuple[tuple[str, str], ...]
    gates: tuple[ReleaseGate, ...]
    qualification_preflight_sha256: str | None = None

    def __post_init__(self) -> None:
        if any(
            not value.strip() for value in (self.version, self.schema_version, self.source_revision)
        ):
            raise ContractViolation("release manifest identity is incomplete")
        artifact_names = tuple(name for name, _ in self.artifacts)
        if not artifact_names or len(artifact_names) != len(set(artifact_names)):
            raise ContractViolation("release manifest artifacts must be non-empty and unique")
        for name, digest in self.artifacts:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ContractViolation("release artifact path must be safe and relative")
            if _SHA256.fullmatch(digest) is None:
                raise ContractViolation("release artifact digest must be SHA-256")
        gate_ids = tuple(gate.gate_id for gate in self.gates)
        if len(gate_ids) != len(set(gate_ids)):
            raise ContractViolation("release gate ids must be unique")
        if (
            self.qualification_preflight_sha256 is not None
            and _SHA256.fullmatch(self.qualification_preflight_sha256) is None
        ):
            raise ContractViolation("qualification preflight digest must be SHA-256")

    @property
    def releasable(self) -> bool:
        revision_is_traceable = self.source_revision not in {
            "workspace-unversioned",
            "unknown",
            "unversioned",
        }
        return (
            revision_is_traceable
            and set(gate.gate_id for gate in self.gates) == set(REQUIRED_RELEASE_GATE_IDS)
            and all(gate.status == GateStatus.PASSED for gate in self.gates)
        )

    def write(self, path: str | Path) -> Path:
        destination = Path(path)
        payload = {
            "version": self.version,
            "schema_version": self.schema_version,
            "source_revision": self.source_revision,
            "releasable": self.releasable,
            "qualification_preflight_sha256": self.qualification_preflight_sha256,
            "artifacts": dict(self.artifacts),
            "gates": [
                {"gate_id": gate.gate_id, "status": gate.status.value, "evidence": gate.evidence}
                for gate in self.gates
            ],
        }
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return destination

    @classmethod
    def load(cls, path: str | Path) -> ReleaseManifest:
        try:
            payload: Any = parse_json_text(
                read_text_limited(
                    path,
                    DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
                    "release manifest",
                )
            )
            if not isinstance(payload, dict):
                raise ContractViolation("release manifest must be an object")
            raw_artifacts = payload.get("artifacts")
            raw_gates = payload.get("gates")
            if not isinstance(raw_artifacts, dict) or not isinstance(raw_gates, list):
                raise ContractViolation("release manifest collections are invalid")
            artifacts: list[tuple[str, str]] = []
            for name, digest in raw_artifacts.items():
                if not isinstance(name, str) or not isinstance(digest, str):
                    raise ContractViolation("release artifact entry is invalid")
                artifacts.append((name, digest))
            gates: list[ReleaseGate] = []
            for item in raw_gates:
                if not isinstance(item, dict):
                    raise ContractViolation("release gate entry is invalid")
                gate_id = item.get("gate_id")
                status_value = item.get("status")
                evidence = item.get("evidence")
                if (
                    not isinstance(gate_id, str)
                    or not isinstance(status_value, str)
                    or not isinstance(evidence, str)
                ):
                    raise ContractViolation("release gate fields are invalid")
                gates.append(ReleaseGate(gate_id, GateStatus(status_value), evidence))
            preflight_digest = payload.get("qualification_preflight_sha256")
            if preflight_digest is not None and not isinstance(preflight_digest, str):
                raise ContractViolation("qualification preflight digest is invalid")
            return cls(
                _required_string(payload, "version"),
                _required_string(payload, "schema_version"),
                _required_string(payload, "source_revision"),
                tuple(artifacts),
                tuple(gates),
                preflight_digest,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ContractViolation(f"invalid release manifest: {exc}") from exc

    def verify(
        self,
        root: str | Path,
        *,
        manifest_name: str = "release-manifest.json",
    ) -> None:
        expected = dict(self.artifacts)
        actual = dict(hash_bundle_tree(root, exclude=(manifest_name,)))
        if expected.keys() != actual.keys():
            missing = sorted(expected.keys() - actual.keys())
            unexpected = sorted(actual.keys() - expected.keys())
            raise ContractViolation(
                f"release bundle file set mismatch; missing={missing}, unexpected={unexpected}"
            )
        for name, digest in expected.items():
            if actual[name] != digest:
                raise ContractViolation(f"release artifact digest mismatch: {name}")
        if self.qualification_preflight_sha256 is not None:
            preflight = expected.get("qualification/preflight.json")
            if preflight != self.qualification_preflight_sha256:
                raise ContractViolation("release preflight digest is not bound to the bundle")


def hash_artifacts(
    root: str | Path, relative_paths: tuple[str, ...]
) -> tuple[tuple[str, str], ...]:
    base = Path(root).resolve()
    result: list[tuple[str, str]] = []
    for relative in relative_paths:
        candidate = (base / relative).resolve()
        if candidate.parent != base and base not in candidate.parents:
            raise ContractViolation("release artifact path escapes root")
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        result.append((relative.replace("\\", "/"), _sha256_file(candidate)))
    return tuple(result)


def hash_bundle_tree(
    root: str | Path,
    *,
    exclude: tuple[str, ...] = ("release-manifest.json",),
) -> tuple[tuple[str, str], ...]:
    base = Path(root).resolve()
    if not base.is_dir():
        raise ContractViolation("release bundle root is not a directory")
    excluded = {_safe_relative_path(item) for item in exclude}
    result: list[tuple[str, str]] = []
    for candidate in sorted(base.rglob("*"), key=lambda item: item.as_posix()):
        relative = candidate.relative_to(base).as_posix()
        if _is_reparse_point(candidate):
            raise ContractViolation(f"release bundle contains a link or reparse point: {relative}")
        if candidate.is_dir():
            continue
        try:
            mode = candidate.stat().st_mode
        except OSError as exc:
            raise ContractViolation(f"cannot inspect release bundle entry: {relative}") from exc
        if not stat.S_ISREG(mode):
            raise ContractViolation(f"release bundle entry is not a regular file: {relative}")
        resolved = candidate.resolve()
        if base not in resolved.parents:
            raise ContractViolation(f"release bundle entry escapes root: {relative}")
        if relative not in excluded:
            result.append((relative, _sha256_file(candidate)))
    if not result:
        raise ContractViolation("release bundle contains no artifacts")
    return tuple(result)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ContractViolation(f"cannot hash release artifact: {path}") from exc
    return digest.hexdigest()


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ContractViolation("release exclusion path must be safe and relative")
    return path.as_posix()


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise ContractViolation(f"cannot inspect release bundle entry: {path}") from exc
    attributes = getattr(metadata, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & 0x400)


def _required_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ContractViolation(f"release manifest {field} is invalid")
    return value
