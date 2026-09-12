from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    parse_json_text,
    read_text_limited,
)
from uga.core.errors import ContractViolation
from uga.release.manifest import GateStatus, ReleaseManifest
from uga.release.revision import validate_source_revision

REQUIRED_BUILD_CHECKS = (
    "npm-clean-install",
    "typescript-typecheck",
    "typescript-build",
    "python-locked-install",
    "ruff",
    "mypy-strict",
    "pytest",
    "pep517-wheel-sdist",
    "cargo-release-locked",
    "wheel-clean-install",
    "installed-command-smoke",
    "dependency-inventory",
    "bundle-manifest-verify",
    "bundle-launcher-smoke",
)

_SHA256_LENGTH = 64


@dataclass(frozen=True, slots=True)
class BuildEvidenceArtifact:
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
            raise ContractViolation("build evidence artifact path must be safe and relative")
        if len(self.sha256) != _SHA256_LENGTH or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ContractViolation("build evidence artifact digest must be SHA-256")


@dataclass(frozen=True, slots=True)
class BuildQualificationReport:
    SCHEMA_NAME: ClassVar[str] = "uga.build_qualification"
    SCHEMA_VERSION: ClassVar[str] = "1.1"

    source_revision: str
    bundle_name: str
    checks: tuple[str, ...]
    artifacts: tuple[BuildEvidenceArtifact, ...]
    passed: bool = True

    def __post_init__(self) -> None:
        validate_source_revision(self.source_revision)
        if not self.bundle_name.strip() or Path(self.bundle_name).name != self.bundle_name:
            raise ContractViolation("build evidence bundle name is invalid")
        if self.checks != REQUIRED_BUILD_CHECKS:
            raise ContractViolation("build evidence does not contain the required ordered checks")
        if type(self.passed) is not bool or not self.passed:
            raise ContractViolation("build evidence may only be emitted after every check passes")
        paths = tuple(item.relative_path for item in self.artifacts)
        if len(paths) != len(set(paths)):
            raise ContractViolation("build evidence artifact paths must be unique")
        required_names = {
            "release-manifest.json",
            "native/uga_capture.dll",
            "run_uga.ps1",
            "third-party-inventory.json",
        }
        if not required_names.issubset(paths):
            raise ContractViolation("build evidence is missing required bundle artifacts")
        if sum(path.endswith(".whl") for path in paths) != 1:
            raise ContractViolation("build evidence requires exactly one wheel")
        if sum(path.endswith(".tar.gz") for path in paths) != 1:
            raise ContractViolation("build evidence requires exactly one source archive")

    def verify(self, bundle_root: str | Path) -> None:
        root = Path(bundle_root).resolve()
        if root.name != self.bundle_name:
            raise ContractViolation("build evidence bundle name does not match its bundle")
        manifest = ReleaseManifest.load(root / "release-manifest.json")
        manifest.verify(root)
        if manifest.source_revision != self.source_revision:
            raise ContractViolation("build evidence source revision does not match bundle manifest")
        statuses = {gate.gate_id: gate.status for gate in manifest.gates}
        for gate_id in ("automated-tests", "package-build", "package-install-smoke"):
            if statuses.get(gate_id) != GateStatus.PASSED:
                raise ContractViolation(f"build evidence requires passed manifest gate: {gate_id}")
        for artifact in self.artifacts:
            candidate = (root / artifact.relative_path).resolve()
            if root not in candidate.parents or not candidate.is_file():
                raise ContractViolation(
                    f"build evidence artifact is missing: {artifact.relative_path}"
                )
            if _sha256_file(candidate) != artifact.sha256:
                raise ContractViolation(
                    f"build evidence artifact digest mismatch: {artifact.relative_path}"
                )
        _verify_dependency_inventory(root / "third-party-inventory.json")

    def write(self, path: str | Path) -> Path:
        destination = Path(path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": self.SCHEMA_NAME,
            "schema_version": self.SCHEMA_VERSION,
            "source_revision": self.source_revision,
            "bundle_name": self.bundle_name,
            "passed": self.passed,
            "checks": [{"check_id": check, "status": "passed"} for check in self.checks],
            "artifacts": [asdict(artifact) for artifact in self.artifacts],
        }
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return destination

    @classmethod
    def load(cls, path: str | Path) -> BuildQualificationReport:
        try:
            payload: Any = parse_json_text(
                read_text_limited(
                    path,
                    DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
                    "build qualification report",
                )
            )
            if (
                not isinstance(payload, dict)
                or payload.get("schema") != cls.SCHEMA_NAME
                or payload.get("schema_version") != cls.SCHEMA_VERSION
            ):
                raise ContractViolation("unsupported build qualification report envelope")
            raw_checks = payload.get("checks")
            raw_artifacts = payload.get("artifacts")
            if not isinstance(raw_checks, list) or not isinstance(raw_artifacts, list):
                raise ContractViolation("build qualification report entries are invalid")
            checks: list[str] = []
            for item in raw_checks:
                if (
                    not isinstance(item, dict)
                    or type(item.get("check_id")) is not str
                    or item.get("status") != "passed"
                ):
                    raise ContractViolation("build qualification check is invalid")
                checks.append(item["check_id"])
            artifacts: list[BuildEvidenceArtifact] = []
            for item in raw_artifacts:
                if (
                    not isinstance(item, dict)
                    or type(item.get("relative_path")) is not str
                    or type(item.get("sha256")) is not str
                ):
                    raise ContractViolation("build qualification artifact is invalid")
                artifacts.append(BuildEvidenceArtifact(item["relative_path"], item["sha256"]))
            source_revision = payload.get("source_revision")
            bundle_name = payload.get("bundle_name")
            passed = payload.get("passed")
            if type(source_revision) is not str or type(bundle_name) is not str:
                raise ContractViolation("build qualification identity is invalid")
            if not isinstance(passed, bool):
                raise ContractViolation("build qualification pass status is invalid")
            return cls(
                source_revision,
                bundle_name,
                tuple(checks),
                tuple(artifacts),
                passed,
            )
        except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError) as exc:
            raise ContractViolation(f"invalid build qualification report: {exc}") from exc


def build_qualification_report(
    bundle_root: str | Path,
    *,
    source_revision: str,
    launcher_smoke_passed: bool,
) -> BuildQualificationReport:
    if not launcher_smoke_passed:
        raise ContractViolation("build qualification requires the real bundle launcher smoke")
    root = Path(bundle_root).resolve()
    wheels = tuple(sorted(path for path in root.glob("*.whl") if path.is_file()))
    archives = tuple(sorted(path for path in root.glob("*.tar.gz") if path.is_file()))
    if len(wheels) != 1 or len(archives) != 1:
        raise ContractViolation("build qualification requires one wheel and one source archive")
    relative_paths = (
        "release-manifest.json",
        "native/uga_capture.dll",
        "run_uga.ps1",
        "third-party-inventory.json",
        wheels[0].name,
        archives[0].name,
    )
    report = BuildQualificationReport(
        source_revision,
        root.name,
        REQUIRED_BUILD_CHECKS,
        tuple(
            BuildEvidenceArtifact(relative_path, _sha256_file(root / relative_path))
            for relative_path in relative_paths
        ),
    )
    report.verify(root)
    return report


def _verify_dependency_inventory(path: Path) -> None:
    payload: Any = parse_json_text(
        read_text_limited(
            path,
            DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
            "dependency inventory",
        )
    )
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "uga.dependency_inventory"
        or payload.get("schema_version") != "1.1"
        or payload.get("license_declarations_complete") is not True
        or payload.get("unknown_license_declarations") != []
    ):
        raise ContractViolation("dependency inventory has incomplete license declarations")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
