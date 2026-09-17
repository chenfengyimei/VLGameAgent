from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    ArtifactResourceLimits,
    parse_json_text,
    read_text_limited,
    sha256_file_limited,
)
from uga.core.errors import ContractViolation
from uga.recording.json_codec import canonical_json
from uga.recording.parquet_io import read_rows

_REQUIRED_FILES = frozenset(
    {
        "metadata.json",
        "timeline.parquet",
        "actions.parquet",
        "observations.parquet",
        "provenance.parquet",
        "tasks.json",
        "events.jsonl",
        "annotations.jsonl",
        "checksum.json",
    }
)
_CHECKSUM_NAME = "checksum.json"
_EXECUTION_RECEIPTS_NAME = "execution_receipts.parquet"


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    sequence: int
    timestamp_ns: int
    elapsed_ns: int
    kind: str
    reference_id: str
    payload: object


@dataclass(frozen=True, slots=True)
class ReplayValidation:
    event_count: int
    action_count: int
    observation_count: int
    digest: str


class ReplayEngine:
    """Read-only deterministic episode replay on the recorded monotonic timeline."""

    def __init__(
        self,
        episode_path: str | Path,
        *,
        verify_checksums: bool = True,
        limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
    ) -> None:
        self.path = Path(episode_path).resolve()
        self._limits = limits
        if not self.path.is_dir():
            raise FileNotFoundError(self.path)
        self._verify_resource_limits()
        missing = sorted(name for name in _REQUIRED_FILES if not (self.path / name).is_file())
        if missing:
            raise ContractViolation(f"episode is incomplete; missing: {', '.join(missing)}")
        if verify_checksums:
            self._verify_checksums()
        self.metadata = self._read_json(self.path / "metadata.json")
        if self.metadata.get("schema_version") != "1.1":
            raise ContractViolation("unsupported episode schema version")
        self.timeline = read_rows(self.path / "timeline.parquet", limits=limits)
        self.actions = read_rows(self.path / "actions.parquet", limits=limits)
        self.observations = read_rows(self.path / "observations.parquet", limits=limits)
        self.provenance = read_rows(self.path / "provenance.parquet", limits=limits)
        self.has_execution_receipt_table = (
            self.path / _EXECUTION_RECEIPTS_NAME
        ).is_file()
        self.execution_receipts = (
            read_rows(self.path / _EXECUTION_RECEIPTS_NAME, limits=limits)
            if self.has_execution_receipt_table
            else []
        )
        self._events = self._validate_and_materialize()
        self._cursor = 0

    def validation(self) -> ReplayValidation:
        digest_payload = [
            {
                "sequence": event.sequence,
                "timestamp_ns": event.timestamp_ns,
                "kind": event.kind,
                "reference_id": event.reference_id,
                "payload": event.payload,
            }
            for event in self._events
        ]
        digest = hashlib.sha256(canonical_json(digest_payload).encode("utf-8")).hexdigest()
        return ReplayValidation(
            len(self._events), len(self.actions), len(self.observations), digest
        )

    def reset(self) -> None:
        self._cursor = 0

    def seek_elapsed(self, elapsed_ns: int) -> int:
        if elapsed_ns < 0:
            raise ContractViolation("replay seek cannot use negative time")
        low = 0
        high = len(self._events)
        while low < high:
            middle = (low + high) // 2
            if self._events[middle].elapsed_ns < elapsed_ns:
                low = middle + 1
            else:
                high = middle
        self._cursor = low
        return self._cursor

    def next_event(self) -> ReplayEvent | None:
        if self._cursor >= len(self._events):
            return None
        event = self._events[self._cursor]
        self._cursor += 1
        return event

    def events_until(self, elapsed_ns: int) -> tuple[ReplayEvent, ...]:
        if elapsed_ns < 0:
            raise ContractViolation("replay time cannot be negative")
        result: list[ReplayEvent] = []
        while self._cursor < len(self._events):
            event = self._events[self._cursor]
            if event.elapsed_ns > elapsed_ns:
                break
            result.append(event)
            self._cursor += 1
        return tuple(result)

    def actions_for_observation(self, observation_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            action for action in self.actions if action.get("observation_id") == observation_id
        )

    def provenance_for_action(self, action_id: str) -> dict[str, Any] | None:
        return next(
            (row for row in self.provenance if row.get("action_id") == action_id),
            None,
        )

    def receipts_for_action(self, action_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            row for row in self.execution_receipts if row.get("action_id") == action_id
        )

    def receipts_for_proposal(self, proposal_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(
            row for row in self.execution_receipts if row.get("proposal_id") == proposal_id
        )

    def _validate_and_materialize(self) -> tuple[ReplayEvent, ...]:
        ordered = sorted(
            self.timeline,
            key=lambda row: (int(row["timestamp_ns"]), int(row["sequence"])),
        )
        if ordered != self.timeline:
            raise ContractViolation("timeline parquet is not in deterministic time order")
        action_counts = Counter(str(row["action_id"]) for row in self.actions)
        provenance_counts = Counter(str(row["action_id"]) for row in self.provenance)
        if action_counts != provenance_counts or any(
            count != 1 for count in action_counts.values()
        ):
            raise ContractViolation("every recorded action must have exactly one provenance row")
        observation_ids = {str(row["observation_id"]) for row in self.observations}
        observation_times = {
            str(row["observation_id"]): int(row["timestamp_ns"])
            for row in self.observations
        }
        for action in self.actions:
            observation_id = action.get("observation_id")
            if observation_id is not None and observation_id not in observation_ids:
                raise ContractViolation(f"action references missing observation: {observation_id}")
        action_by_id = {str(row["action_id"]): row for row in self.actions}
        provenance_by_id = {
            str(row["action_id"]): row for row in self.provenance
        }
        if len(observation_ids) != len(self.observations):
            raise ContractViolation("Episode contains duplicate observation identifiers")
        for row in self.provenance:
            parent_id = row.get("parent_action_id")
            if parent_id is not None:
                parent = action_by_id.get(str(parent_id))
                if parent is None or parent.get("action_layer") == "physical":
                    raise ContractViolation("physical child references an invalid logical parent")
                if row.get("proposal_id") != provenance_by_id[str(parent_id)].get("proposal_id"):
                    raise ContractViolation("child and logical parent proposal identities differ")
        receipt_counts = Counter(
            str(row["action_id"]) for row in self.execution_receipts
        )
        if any(count != 1 for count in receipt_counts.values()):
            raise ContractViolation("every physical action can have at most one terminal receipt")
        valid_statuses = {"executed", "rejected", "expired", "flushed"}
        for receipt in self.execution_receipts:
            action_id = str(receipt.get("action_id", ""))
            receipt_action = action_by_id.get(action_id)
            if receipt_action is None or receipt_action.get("action_layer") != "physical":
                raise ContractViolation(
                    f"execution receipt references a non-physical action: {action_id}"
                )
            receipt_provenance = provenance_by_id[action_id]
            recorded_proposal = receipt_provenance.get("proposal_id")
            if recorded_proposal is not None and (
                receipt.get("proposal_id") != recorded_proposal
            ):
                raise ContractViolation("execution receipt proposal identity does not match")
            if receipt.get("lease_id") != receipt_provenance.get("lease_id"):
                raise ContractViolation("execution receipt lease identity does not match")
            if str(receipt.get("status")) not in valid_statuses:
                raise ContractViolation("execution receipt has an invalid status")
            if not str(receipt.get("proposal_id", "")).strip():
                raise ContractViolation("execution receipt proposal id is missing")
            at_ns = int(receipt["at_ns"])
            if receipt.get("status") == "executed" and not (
                int(receipt_action["effective_from_ns"]) <= at_ns
                <= int(receipt_action["expires_at_ns"])
            ):
                raise ContractViolation("executed receipt is outside the action lifetime")
            if at_ns < 0:
                raise ContractViolation("execution receipt timestamp is negative")
            inference_id = receipt.get("inference_observation_id")
            if inference_id is not None and str(inference_id) not in observation_ids:
                raise ContractViolation(
                    "execution receipt references missing inference observation"
                )
            pre_id = receipt.get("pre_action_observation_id")
            captured = receipt.get("pre_action_capture_ns")
            if (pre_id is None) != (captured is None):
                raise ContractViolation("pre-action observation binding is incomplete")
            if pre_id is not None:
                if str(pre_id) not in observation_times:
                    raise ContractViolation(
                        "execution receipt references missing pre-action observation"
                    )
                if type(captured) is not int or not (
                    0 <= captured <= observation_times[str(pre_id)] <= at_ns
                ):
                    raise ContractViolation("pre-action observation must precede execution")
            execution_id = receipt.get("effect_observation_id") or receipt.get(
                "execution_observation_id"
            )
            if execution_id is not None:
                execution_key = str(execution_id)
                if execution_key not in observation_ids:
                    raise ContractViolation(
                        "execution receipt references missing execution observation"
                    )
                if observation_times[execution_key] < at_ns:
                    raise ContractViolation(
                        "execution observation predates its terminal receipt"
                    )

        materialized: list[ReplayEvent] = []
        previous: tuple[int, int] | None = None
        for row in ordered:
            timestamp_ns = int(row["timestamp_ns"])
            elapsed_ns = int(row["elapsed_ns"])
            sequence = int(row["sequence"])
            if timestamp_ns < 0 or elapsed_ns < 0:
                raise ContractViolation("replay timeline contains a negative timestamp")
            order_key = (timestamp_ns, sequence)
            if previous is not None and order_key <= previous:
                raise ContractViolation("replay timeline order is not strictly increasing")
            previous = order_key
            materialized.append(
                ReplayEvent(
                    sequence,
                    timestamp_ns,
                    elapsed_ns,
                    str(row["kind"]),
                    str(row["reference_id"]),
                    parse_json_text(str(row["payload_json"])),
                )
            )
        return tuple(materialized)

    def _verify_checksums(self) -> None:
        document = self._read_json(self.path / "checksum.json")
        if document.get("algorithm") != "sha256" or not isinstance(document.get("files"), dict):
            raise ContractViolation("invalid checksum manifest")
        declared = document["files"]
        actual_files = {
            path.relative_to(self.path).as_posix()
            for path in self.path.rglob("*")
            if path.is_file() and path != self.path / _CHECKSUM_NAME
        }
        declared_files = {str(relative) for relative in declared}
        if declared_files != actual_files:
            missing = sorted(actual_files - declared_files)
            extra = sorted(declared_files - actual_files)
            raise ContractViolation(
                f"checksum manifest file set mismatch; missing={missing}, extra={extra}"
            )
        for relative, expected in declared.items():
            candidate = (self.path / str(relative)).resolve()
            if candidate.parent != self.path and self.path not in candidate.parents:
                raise ContractViolation("checksum manifest contains an unsafe path")
            if not candidate.is_file():
                raise ContractViolation(f"checksummed file is missing: {relative}")
            if not isinstance(expected, str) or len(expected) != 64:
                raise ContractViolation(f"invalid checksum digest: {relative}")
            actual = sha256_file_limited(
                candidate,
                self._file_limit(candidate),
                f"Episode file {relative}",
            )
            if actual != expected:
                raise ContractViolation(f"checksum mismatch: {relative}")

    def _verify_resource_limits(self) -> None:
        file_count = 0
        total_bytes = 0
        for path in self.path.rglob("*"):
            if not path.is_file():
                continue
            file_count += 1
            if file_count > self._limits.max_episode_files:
                raise ContractViolation("Episode exceeds the file-count resource limit")
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise ContractViolation(f"cannot inspect Episode file: {path}") from exc
            if size > self._file_limit(path):
                raise ContractViolation(f"Episode file exceeds its resource limit: {path.name}")
            total_bytes += size
            if total_bytes > self._limits.max_episode_bytes:
                raise ContractViolation("Episode exceeds the total byte resource limit")

    def _file_limit(self, path: Path) -> int:
        if path.name == _CHECKSUM_NAME and path.parent == self.path:
            return self._limits.max_checksum_bytes
        if path.suffix == ".parquet":
            return self._limits.max_parquet_file_bytes
        if path.name == "video.mp4":
            return self._limits.max_video_bytes
        if path.suffix == ".jsonl":
            return self._limits.max_jsonl_bytes
        return self._limits.max_document_bytes

    def _read_json(self, path: Path) -> dict[str, Any]:
        maximum = (
            self._limits.max_checksum_bytes
            if path.name == _CHECKSUM_NAME
            else self._limits.max_document_bytes
        )
        value = parse_json_text(read_text_limited(path, maximum, path.name))
        if not isinstance(value, dict):
            raise ContractViolation(f"expected JSON object: {path.name}")
        return value
