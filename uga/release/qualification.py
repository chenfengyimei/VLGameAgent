from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.release.manifest import (
    REQUIRED_RELEASE_GATE_IDS,
    GateStatus,
    ReleaseGate,
    ReleaseManifest,
    hash_artifacts,
)

REQUIRED_GATE_IDS = REQUIRED_RELEASE_GATE_IDS
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class EvidenceArtifact:
    relative_path: str
    sha256: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        if (
            not self.relative_path.strip()
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in self.relative_path
        ):
            raise ContractViolation("qualification evidence path must be safe and relative")
        if _SHA256.fullmatch(self.sha256) is None:
            raise ContractViolation("qualification evidence digest must be SHA-256")


@dataclass(frozen=True, slots=True)
class QualificationRecord:
    gate_id: str
    status: GateStatus
    evidence: str
    artifacts: tuple[EvidenceArtifact, ...] = ()

    def __post_init__(self) -> None:
        if self.gate_id not in REQUIRED_GATE_IDS or not self.evidence.strip():
            raise ContractViolation("qualification record is invalid")
        if self.status == GateStatus.PASSED and not self.artifacts:
            raise ContractViolation("passed qualification gate requires hashed evidence artifacts")
        if (
            self.gate_id == "repository-license"
            and self.status == GateStatus.PASSED
            and not any(
                PurePosixPath(item.relative_path).name.startswith("LICENSE")
                for item in self.artifacts
            )
        ):
            raise ContractViolation("repository license gate requires a LICENSE evidence artifact")


@dataclass(frozen=True, slots=True)
class QualificationLedger(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.qualification_ledger"

    source_revision: str
    records: tuple[QualificationRecord, ...]

    def __post_init__(self) -> None:
        if not self.source_revision.strip():
            raise ContractViolation("qualification ledger requires a source revision")
        ids = tuple(record.gate_id for record in self.records)
        if len(ids) != len(set(ids)) or set(ids) != set(REQUIRED_GATE_IDS):
            raise ContractViolation("qualification ledger must contain every required gate once")

    @classmethod
    def initialize(cls, source_revision: str) -> QualificationLedger:
        return cls(
            source_revision,
            tuple(
                QualificationRecord(gate_id, GateStatus.NOT_RUN, "evidence not recorded")
                for gate_id in REQUIRED_GATE_IDS
            ),
        )

    @property
    def releasable(self) -> bool:
        revision_is_traceable = self.source_revision not in {
            "workspace-unversioned",
            "unknown",
            "unversioned",
        }
        return revision_is_traceable and all(
            record.status == GateStatus.PASSED for record in self.records
        )

    def with_record(self, record: QualificationRecord) -> QualificationLedger:
        return replace(
            self,
            records=tuple(
                record if current.gate_id == record.gate_id else current for current in self.records
            ),
        )

    def verify_artifacts(self, root: str | Path) -> None:
        base = Path(root).resolve()
        for record in self.records:
            for artifact in record.artifacts:
                candidate = (base / artifact.relative_path).resolve()
                if base not in candidate.parents or not candidate.is_file():
                    raise ContractViolation(
                        f"qualification evidence is missing: {artifact.relative_path}"
                    )
                actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
                if actual != artifact.sha256:
                    raise ContractViolation(
                        f"qualification evidence digest mismatch: {artifact.relative_path}"
                    )

    def release_gates(self, evidence_root: str | Path) -> tuple[ReleaseGate, ...]:
        self.verify_artifacts(evidence_root)
        return tuple(
            ReleaseGate(record.gate_id, record.status, record.evidence) for record in self.records
        )

    def write(self, path: str | Path) -> Path:
        destination = Path(path)
        payload = {
            "schema": self.SCHEMA_NAME,
            "schema_version": self.SCHEMA_VERSION,
            "data": {
                "source_revision": self.source_revision,
                "releasable": self.releasable,
                "records": [
                    {
                        "gate_id": record.gate_id,
                        "status": record.status.value,
                        "evidence": record.evidence,
                        "artifacts": [
                            {"relative_path": item.relative_path, "sha256": item.sha256}
                            for item in record.artifacts
                        ],
                    }
                    for record in self.records
                ],
            },
        }
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return destination

    @classmethod
    def load(cls, path: str | Path) -> QualificationLedger:
        payload: Any = json.loads(Path(path).read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != cls.SCHEMA_NAME
            or payload.get("schema_version") != cls.SCHEMA_VERSION
            or not isinstance(payload.get("data"), dict)
        ):
            raise ContractViolation("unsupported qualification ledger envelope")
        data: dict[str, Any] = payload["data"]
        raw_records = data.get("records")
        if not isinstance(raw_records, list):
            raise ContractViolation("qualification ledger records are invalid")
        records: list[QualificationRecord] = []
        for item in raw_records:
            if not isinstance(item, dict) or not isinstance(item.get("artifacts"), list):
                raise ContractViolation("qualification record entry is invalid")
            records.append(
                QualificationRecord(
                    str(item["gate_id"]),
                    GateStatus(str(item["status"])),
                    str(item["evidence"]),
                    tuple(
                        EvidenceArtifact(str(artifact["relative_path"]), str(artifact["sha256"]))
                        for artifact in item["artifacts"]
                        if isinstance(artifact, dict)
                    ),
                )
            )
            if len(records[-1].artifacts) != len(item["artifacts"]):
                raise ContractViolation("qualification artifact entry must be an object")
        return cls(str(data["source_revision"]), tuple(records))


def hash_evidence(
    root: str | Path, relative_paths: tuple[str, ...]
) -> tuple[EvidenceArtifact, ...]:
    return tuple(
        EvidenceArtifact(relative, digest)
        for relative, digest in hash_artifacts(root, relative_paths)
    )


def build_qualified_release_manifest(
    bundle_root: str | Path,
    ledger: QualificationLedger,
    evidence_root: str | Path,
    *,
    version: str,
) -> ReleaseManifest:
    root = Path(bundle_root).resolve()
    wheels = tuple(path for path in root.glob("*.whl") if path.is_file())
    source_archives = tuple(path for path in root.glob("*.tar.gz") if path.is_file())
    if len(wheels) != 1 or len(source_archives) != 1:
        raise ContractViolation("bundle requires exactly one wheel and one source archive")
    artifacts = hash_artifacts(
        root,
        (
            wheels[0].relative_to(root).as_posix(),
            source_archives[0].relative_to(root).as_posix(),
            "native/uga_capture.dll",
            "third-party-inventory.json",
        ),
    )
    return ReleaseManifest(
        version,
        "1.1",
        ledger.source_revision,
        artifacts,
        ledger.release_gates(evidence_root),
    )
