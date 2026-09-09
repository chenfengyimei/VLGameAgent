from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    ArtifactResourceLimits,
    read_text_limited,
    sha256_file_limited,
)
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin
from uga.recording.json_codec import to_json_value

_SHA256 = re.compile(r"[0-9a-f]{64}")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class TrainingArtifactManifest(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.training_artifact"

    artifact_id: str
    model_file: str
    action_schema_version: str
    dataset_manifest: str
    source_revision: str
    training_config: str
    samples_file: str
    base_model: str
    model_sha256: str
    dataset_manifest_sha256: str
    training_config_sha256: str
    samples_sha256: str
    metrics: tuple[tuple[str, float], ...]
    license_metadata: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        required = (
            self.artifact_id,
            self.model_file,
            self.action_schema_version,
            self.dataset_manifest,
            self.source_revision,
            self.training_config,
            self.samples_file,
            self.base_model,
        )
        if any(not value.strip() for value in required):
            raise ContractViolation("training artifact manifest is incomplete")
        if self.action_schema_version != "1.1":
            raise ContractViolation("training artifact action schema must be V1.1")
        model_path = PurePosixPath(self.model_file)
        if model_path.is_absolute() or ".." in model_path.parts or "\\" in self.model_file:
            raise ContractViolation("training artifact model path must be safe and relative")
        digests = (
            self.model_sha256,
            self.dataset_manifest_sha256,
            self.training_config_sha256,
            self.samples_sha256,
        )
        if any(_SHA256.fullmatch(value) is None for value in digests):
            raise ContractViolation("training artifact digests must be SHA-256")
        metric_names = tuple(name for name, _ in self.metrics)
        if (
            not self.metrics
            or len(metric_names) != len(set(metric_names))
            or any(not name.strip() or not math.isfinite(value) for name, value in self.metrics)
        ):
            raise ContractViolation("training artifact metrics are invalid")
        if not self.license_metadata:
            raise ContractViolation("training artifact requires license metadata")
        license_names = tuple(name for name, _ in self.license_metadata)
        if len(license_names) != len(set(license_names)) or any(
            not name.strip() or not value.strip() for name, value in self.license_metadata
        ):
            raise ContractViolation("training artifact license metadata is invalid")

    def write(self, path: str | Path) -> Path:
        destination = Path(path)
        payload = to_json_value(self)
        destination.write_text(
            json.dumps(
                {
                    "schema": self.SCHEMA_NAME,
                    "schema_version": self.SCHEMA_VERSION,
                    "data": payload,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return destination

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
    ) -> TrainingArtifactManifest:
        try:
            payload: Any = json.loads(
                read_text_limited(
                    path,
                    limits.max_document_bytes,
                    "training artifact manifest",
                )
            )
            if (
                not isinstance(payload, dict)
                or payload.get("schema") != cls.SCHEMA_NAME
                or payload.get("schema_version") != cls.SCHEMA_VERSION
                or not isinstance(payload.get("data"), dict)
            ):
                raise ContractViolation("unsupported training artifact envelope")
            data: dict[str, Any] = payload["data"]
            raw_metrics = data["metrics"]
            raw_licenses = data["license_metadata"]
            if not isinstance(raw_metrics, list) or len(raw_metrics) > limits.max_artifact_metrics:
                raise ContractViolation("training artifact exceeds the metrics resource limit")
            if (
                not isinstance(raw_licenses, list)
                or len(raw_licenses) > limits.max_artifact_licenses
            ):
                raise ContractViolation("training artifact exceeds the license resource limit")
            return cls(
                str(data["artifact_id"]),
                str(data["model_file"]),
                str(data["action_schema_version"]),
                str(data["dataset_manifest"]),
                str(data["source_revision"]),
                str(data["training_config"]),
                str(data["samples_file"]),
                str(data["base_model"]),
                str(data["model_sha256"]),
                str(data["dataset_manifest_sha256"]),
                str(data["training_config_sha256"]),
                str(data["samples_sha256"]),
                tuple((str(name), float(value)) for name, value in raw_metrics),
                tuple((str(name), str(value)) for name, value in raw_licenses),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ContractViolation(f"invalid training artifact manifest: {error}") from error

    def verify(
        self,
        output_directory: str | Path,
        *,
        limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
    ) -> None:
        root = Path(output_directory).resolve()
        references = (
            (
                (root / self.model_file).resolve(),
                self.model_sha256,
                True,
                limits.max_artifact_model_bytes,
                "training model",
            ),
            (
                Path(self.dataset_manifest).resolve(),
                self.dataset_manifest_sha256,
                False,
                limits.max_document_bytes,
                "Dataset Manifest",
            ),
            (
                Path(self.training_config).resolve(),
                self.training_config_sha256,
                False,
                limits.max_config_bytes,
                "training config",
            ),
            (
                Path(self.samples_file).resolve(),
                self.samples_sha256,
                False,
                limits.max_jsonl_bytes,
                "training samples",
            ),
        )
        for path, expected, must_be_inside_root, maximum, label in references:
            if must_be_inside_root and root not in path.parents:
                raise ContractViolation("training model resolves outside its artifact directory")
            if not path.is_file():
                raise ContractViolation(f"training artifact reference is missing: {path}")
            if sha256_file_limited(path, maximum, label) != expected:
                raise ContractViolation(f"training artifact digest mismatch: {path}")
