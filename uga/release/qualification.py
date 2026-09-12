from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from uga.core.artifact_limits import DEFAULT_ARTIFACT_LIMITS, parse_json_text, read_text_limited
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.release.manifest import (
    REQUIRED_RELEASE_GATE_IDS,
    GateStatus,
    ReleaseGate,
    ReleaseManifest,
    hash_artifacts,
    hash_bundle_tree,
)
from uga.release.revision import is_traceable_source_revision, validate_source_revision

REQUIRED_GATE_IDS = REQUIRED_RELEASE_GATE_IDS
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REVISION_BOUND_GATE_IDS = frozenset(
    {
        "package-build",
        "package-install-smoke",
        "capture-soak",
        "control-hardware",
        "recorder-10min",
        "dataset-5h",
        "model-training",
        "generalization-bench",
    }
)


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
    source_revision: str | None = None

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
        if self.status == GateStatus.PASSED:
            if self.source_revision is None:
                raise ContractViolation("passed qualification gate requires a source revision")
            validate_source_revision(self.source_revision)
        elif self.source_revision is not None:
            validate_source_revision(self.source_revision)


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
        if any(
            record.status == GateStatus.PASSED
            and record.source_revision != self.source_revision
            for record in self.records
        ):
            raise ContractViolation("qualification gate source revision does not match ledger")

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
        revision_is_traceable = is_traceable_source_revision(self.source_revision)
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
            revision_claimed = False
            for artifact in record.artifacts:
                candidate = (base / artifact.relative_path).resolve()
                if base not in candidate.parents or not candidate.is_file():
                    raise ContractViolation(
                        f"qualification evidence is missing: {artifact.relative_path}"
                    )
                candidate_digest = hashlib.sha256()
                with candidate.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        candidate_digest.update(block)
                if candidate_digest.hexdigest() != artifact.sha256:
                    raise ContractViolation(
                        f"qualification evidence digest mismatch: {artifact.relative_path}"
                    )
                revision_claimed = revision_claimed or _claims_source_revision(
                    candidate, record.source_revision
                )
            if (
                record.status == GateStatus.PASSED
                and record.gate_id in _REVISION_BOUND_GATE_IDS
                and not revision_claimed
            ):
                raise ContractViolation(
                    f"passed gate {record.gate_id} requires JSON evidence "
                    "bound to its source revision"
                )

    def release_gates(self, evidence_root: str | Path) -> tuple[ReleaseGate, ...]:
        self.verify_artifacts(evidence_root)
        return tuple(
            ReleaseGate(record.gate_id, record.status, record.evidence) for record in self.records
        )

    def canonical_sha256(self) -> str:
        payload = json.dumps(
            {
                "source_revision": self.source_revision,
                "records": self._records_payload(),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def write(self, path: str | Path) -> Path:
        destination = Path(path)
        payload = {
            "schema": self.SCHEMA_NAME,
            "schema_version": self.SCHEMA_VERSION,
            "data": self._data_payload(),
        }
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return destination

    def _data_payload(self) -> dict[str, object]:
        return {
            "source_revision": self.source_revision,
            "releasable": self.releasable,
            "records": self._records_payload(),
        }

    def _records_payload(self) -> list[dict[str, object]]:
        return [
            {
                "gate_id": record.gate_id,
                "status": record.status.value,
                "evidence": record.evidence,
                "source_revision": record.source_revision,
                "artifacts": [
                    {"relative_path": item.relative_path, "sha256": item.sha256}
                    for item in record.artifacts
                ],
            }
            for record in self.records
        ]

    @classmethod
    def load(cls, path: str | Path) -> QualificationLedger:
        payload: Any = parse_json_text(
            read_text_limited(
                path,
                DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
                "qualification ledger",
            )
        )
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
                    (
                        None
                        if item.get("source_revision") is None
                        else str(item["source_revision"])
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


def _claims_source_revision(path: Path, expected: str | None) -> bool:
    if expected is None or path.suffix.casefold() != ".json":
        return False
    try:
        payload: Any = parse_json_text(
            read_text_limited(
                path,
                DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
                "revision-bound qualification evidence",
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ContractViolation):
        return False
    if not isinstance(payload, dict):
        return False
    claimed = payload.get("source_revision")
    data = payload.get("data")
    if claimed is None and isinstance(data, dict):
        claimed = data.get("source_revision")
    return type(claimed) is str and claimed == expected


def build_qualified_release_manifest(
    bundle_root: str | Path,
    ledger: QualificationLedger,
    evidence_root: str | Path,
    *,
    version: str,
    preflight_path: str | Path,
    project_root: str | Path,
) -> ReleaseManifest:
    from uga.release.preflight import (
        QualificationPreflight,
        build_qualification_preflight,
        probe_host,
    )

    root = Path(bundle_root).resolve()
    evidence = Path(evidence_root).resolve()
    project = Path(project_root).resolve()
    preflight_source = Path(preflight_path).resolve()
    qualification_dir = root / "qualification"
    qualification_dir.mkdir(parents=True, exist_ok=True)
    bundled_preflight = qualification_dir / "preflight.json"
    shutil.copy2(preflight_source, bundled_preflight)
    preflight = QualificationPreflight.load(bundled_preflight)
    current_host = probe_host(project)
    if (
        preflight.training_artifact_path is None
        or preflight.dataset_manifest_path is None
        or preflight.dataset_root is None
    ):
        raise ContractViolation("qualified promotion requires training and dataset bindings")
    recomputed = build_qualification_preflight(
        ledger,
        project,
        host=current_host,
        training_artifact_path=preflight.training_artifact_path,
        dataset_manifest_path=preflight.dataset_manifest_path,
        dataset_root=preflight.dataset_root,
    )
    if recomputed != preflight:
        raise ContractViolation("qualification preflight does not match current verified inputs")
    if recomputed.blockers:
        raise ContractViolation("qualification preflight contains blockers")
    if not preflight.ledger_releasable or not ledger.releasable:
        raise ContractViolation("qualification ledger is not releasable")
    if not preflight.worktree_clean or not current_host.worktree_clean:
        raise ContractViolation("release promotion requires a clean source worktree")
    if (
        not preflight.source_revision_matches_ledger
        or current_host.source_revision != preflight.source_revision
        or current_host.source_revision != ledger.source_revision
    ):
        raise ContractViolation("release promotion source revision binding changed")
    if preflight.ledger_sha256 != ledger.canonical_sha256():
        raise ContractViolation("qualification ledger changed after preflight")
    expected_statuses = tuple((record.gate_id, record.status.value) for record in ledger.records)
    if preflight.gate_statuses != expected_statuses:
        raise ContractViolation("qualification gate statuses changed after preflight")
    gates = ledger.release_gates(evidence)

    wheels = tuple(path for path in root.glob("*.whl") if path.is_file())
    source_archives = tuple(path for path in root.glob("*.tar.gz") if path.is_file())
    if len(wheels) != 1 or len(source_archives) != 1:
        raise ContractViolation("bundle requires exactly one wheel and one source archive")
    required = (root / "native" / "uga_capture.dll", root / "third-party-inventory.json")
    if any(not path.is_file() for path in required):
        raise ContractViolation("bundle is missing the native DLL or dependency inventory")
    ledger_source = evidence / "qualification.json"
    if not ledger_source.is_file():
        raise ContractViolation("qualification ledger file is missing")
    if QualificationLedger.load(ledger_source).canonical_sha256() != ledger.canonical_sha256():
        raise ContractViolation("qualification ledger file does not match the verified ledger")
    shutil.copy2(ledger_source, qualification_dir / "qualification.json")
    if QualificationPreflight.load(bundled_preflight) != preflight:
        raise ContractViolation("qualification preflight changed during promotion")
    artifacts = hash_bundle_tree(root)
    final_host = probe_host(project)
    if final_host.source_revision != current_host.source_revision or not final_host.worktree_clean:
        raise ContractViolation("source worktree changed while building release manifest")
    preflight_digest = dict(artifacts)["qualification/preflight.json"]
    return ReleaseManifest(
        version,
        "1.1",
        ledger.source_revision,
        artifacts,
        gates,
        preflight_digest,
    )
