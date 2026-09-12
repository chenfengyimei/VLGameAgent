from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    parse_json_text,
    read_text_limited,
    sha256_file_limited,
)
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.dataset.manifest import DatasetManifest
from uga.release.build_evidence import BuildQualificationReport
from uga.release.manifest import (
    REQUIRED_RELEASE_GATE_IDS,
    GateStatus,
    ReleaseGate,
    ReleaseManifest,
    hash_artifacts,
    hash_bundle_tree,
)
from uga.release.model_qualification import validate_model_qualification_report
from uga.release.revision import is_traceable_source_revision, validate_source_revision
from uga.windows.integrity import IntegrityLevel

REQUIRED_GATE_IDS = REQUIRED_RELEASE_GATE_IDS
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REVISION_BOUND_GATE_IDS = frozenset(
    {
        "automated-tests",
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
                revision_claimed = revision_claimed or _proves_gate_for_revision(
                    candidate,
                    record.source_revision,
                    record.gate_id,
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


def _proves_gate_for_revision(path: Path, expected: str | None, gate_id: str) -> bool:
    if expected is None or path.suffix.casefold() != ".json":
        return False
    try:
        if gate_id in {"automated-tests", "package-build", "package-install-smoke"}:
            report = BuildQualificationReport.load(path)
            return report.source_revision == expected and report.passed
        if gate_id == "dataset-5h":
            manifest = DatasetManifest.load(path)
            return manifest.source_revision == expected and manifest.hours() >= 5.0
        payload: Any = parse_json_text(
            read_text_limited(
                path,
                DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
                "revision-bound qualification evidence",
            )
        )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ContractViolation,
        KeyError,
        TypeError,
        ValueError,
    ):
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("source_revision") != expected:
        return False
    if gate_id == "control-hardware" and payload.get("schema") == "uga.control_qualification":
        return _control_report_proves_gate(path, payload)
    if gate_id in {"capture-soak", "control-hardware", "recorder-10min"}:
        return _fixture_report_proves_gate(payload, gate_id)
    if gate_id == "generalization-bench":
        return _benchmark_report_proves_gate(payload)
    if gate_id == "model-training":
        return _model_report_proves_gate(path, payload)
    return False


def _fixture_report_proves_gate(payload: dict[str, Any], gate_id: str) -> bool:
    if (
        payload.get("schema") != "uga.fixture_qualification"
        or payload.get("schema_version") != "1.1"
        or payload.get("passed") is not True
    ):
        return False
    capture = payload.get("capture")
    control = payload.get("control")
    recorder = payload.get("recorder")
    if not isinstance(capture, dict):
        return False
    if gate_id == "capture-soak":
        return _at_least(capture.get("elapsed_seconds"), 1800.0) and (
            capture.get("timestamp_regressions") == 0
        )
    if gate_id == "recorder-10min":
        if not _at_least(capture.get("elapsed_seconds"), 600.0) or not isinstance(
            recorder, dict
        ):
            return False
        quality = recorder.get("quality")
        replay = recorder.get("replay")
        return (
            isinstance(quality, dict)
            and quality.get("status") == "accepted"
            and isinstance(replay, dict)
        )
    if gate_id == "control-hardware":
        if not isinstance(control, dict):
            return False
        required_exercises = (
            "focus_loss",
            "held_key_fault",
            "uipi_mismatch",
            "emergency_hotkey",
            "watchdog_timeout",
        )
        return all(_control_exercise_passed(name, control.get(name)) for name in required_exercises)
    return False


def _benchmark_report_proves_gate(payload: dict[str, Any]) -> bool:
    games = payload.get("games")
    split_rates = payload.get("split_success_rates")
    policy_digest = payload.get("policy_artifact_sha256")
    if (
        payload.get("schema") != "uga.benchmark_report"
        or payload.get("schema_version") != "1.1"
        or not isinstance(payload.get("runs"), int)
        or payload["runs"] < 1
        or not isinstance(games, list)
        or len(set(item for item in games if isinstance(item, str))) < 4
        or not isinstance(split_rates, list)
        or not isinstance(policy_digest, str)
        or _SHA256.fullmatch(policy_digest) is None
    ):
        return False
    labels = {
        item[0]
        for item in split_rates
        if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str)
    }
    return {"train", "test"}.issubset(labels)


def _control_report_proves_gate(path: Path, payload: dict[str, Any]) -> bool:
    exercises = payload.get("exercises")
    source_revision = payload.get("source_revision")
    if (
        payload.get("schema_version") != "1.1"
        or payload.get("passed") is not True
        or not isinstance(exercises, dict)
        or not isinstance(source_revision, str)
    ):
        return False
    required = {
        "focus_loss",
        "held_key_fault",
        "uipi_mismatch",
        "emergency_hotkey",
        "watchdog_timeout",
    }
    if not (
        set(exercises) == required
        and all(_control_exercise_passed(name, exercises[name]) for name in required)
        and exercises["uipi_mismatch"].get("probe_report_sha256")
        == payload.get("uipi_report_sha256")
    ):
        return False
    for path_field, digest_field, schema in (
        ("fixture_report", "fixture_report_sha256", "uga.fixture_qualification"),
        ("uipi_report", "uipi_report_sha256", "uga.uipi_qualification"),
    ):
        relative = payload.get(path_field)
        expected_digest = payload.get(digest_field)
        if not isinstance(relative, str) or not isinstance(expected_digest, str):
            return False
        candidate = (path.parent / relative).resolve()
        if path.parent not in candidate.parents or not candidate.is_file():
            return False
        try:
            actual_digest = sha256_file_limited(
                candidate,
                DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
                "control qualification source report",
            )
        except (OSError, ContractViolation):
            return False
        if actual_digest != expected_digest:
            return False
        try:
            source_payload: Any = parse_json_text(
                read_text_limited(
                    candidate,
                    DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
                    "control qualification source report",
                )
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ContractViolation):
            return False
        if (
            not isinstance(source_payload, dict)
            or source_payload.get("schema") != schema
            or source_payload.get("schema_version") != "1.1"
            or source_payload.get("source_revision") != source_revision
            or source_payload.get("passed") is not True
        ):
            return False
        if schema == "uga.uipi_qualification" and not _valid_uipi_source_report(
            source_payload
        ):
            return False
        if schema == "uga.fixture_qualification":
            source_control = source_payload.get("control")
            fixture_exercises = required - {"uipi_mismatch"}
            if not isinstance(source_control, dict) or not all(
                _control_exercise_passed(name, source_control.get(name))
                for name in fixture_exercises
            ):
                return False
    return True


def _valid_uipi_source_report(payload: dict[str, Any]) -> bool:
    current = payload.get("current_integrity")
    target = payload.get("target_integrity")
    if not isinstance(current, dict) or not isinstance(target, dict):
        return False
    current_value = current.get("value")
    target_value = target.get("value")
    valid_values = {
        int(level)
        for level in IntegrityLevel
        if level not in {IntegrityLevel.UNKNOWN, IntegrityLevel.PROTECTED}
    }
    return (
        isinstance(current_value, int)
        and not isinstance(current_value, bool)
        and current_value in valid_values
        and isinstance(target_value, int)
        and not isinstance(target_value, bool)
        and target_value in valid_values
        and target_value > current_value
        and payload.get("executed") is False
        and payload.get("reason") == "integrity_incompatible"
        and payload.get("probe_action") == "F24 key-up only"
    )


def _control_exercise_passed(name: str, payload: Any) -> bool:
    if not isinstance(payload, dict) or payload.get("passed") is not True:
        return False
    if name == "emergency_hotkey":
        return payload.get("registered") is True
    return payload.get("exercised") is True


def _model_report_proves_gate(path: Path, payload: dict[str, Any]) -> bool:
    try:
        validate_model_qualification_report(
            path,
            expected_revision=str(payload.get("source_revision", "")),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ContractViolation):
        return False
    return True


def _at_least(value: object, minimum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= minimum
    )


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
        preflight.model_qualification_path is None
        or preflight.dataset_manifest_path is None
        or preflight.dataset_root is None
    ):
        raise ContractViolation(
            "qualified promotion requires five-stage model and dataset bindings"
        )
    recomputed = build_qualification_preflight(
        ledger,
        project,
        host=current_host,
        model_qualification_path=preflight.model_qualification_path,
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
