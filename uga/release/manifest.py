from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

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
            "artifacts": dict(self.artifacts),
            "gates": [
                {"gate_id": gate.gate_id, "status": gate.status.value, "evidence": gate.evidence}
                for gate in self.gates
            ],
        }
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return destination


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
        result.append(
            (relative.replace("\\", "/"), hashlib.sha256(candidate.read_bytes()).hexdigest())
        )
    return tuple(result)
