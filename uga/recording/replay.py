from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

    def __init__(self, episode_path: str | Path, *, verify_checksums: bool = True) -> None:
        self.path = Path(episode_path).resolve()
        if not self.path.is_dir():
            raise FileNotFoundError(self.path)
        missing = sorted(name for name in _REQUIRED_FILES if not (self.path / name).is_file())
        if missing:
            raise ContractViolation(f"episode is incomplete; missing: {', '.join(missing)}")
        if verify_checksums:
            self._verify_checksums()
        self.metadata = self._read_json(self.path / "metadata.json")
        if self.metadata.get("schema_version") != "1.1":
            raise ContractViolation("unsupported episode schema version")
        self.timeline = read_rows(self.path / "timeline.parquet")
        self.actions = read_rows(self.path / "actions.parquet")
        self.observations = read_rows(self.path / "observations.parquet")
        self.provenance = read_rows(self.path / "provenance.parquet")
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
        for action in self.actions:
            observation_id = action.get("observation_id")
            if observation_id is not None and observation_id not in observation_ids:
                raise ContractViolation(f"action references missing observation: {observation_id}")

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
                    json.loads(str(row["payload_json"])),
                )
            )
        return tuple(materialized)

    def _verify_checksums(self) -> None:
        document = self._read_json(self.path / "checksum.json")
        if document.get("algorithm") != "sha256" or not isinstance(document.get("files"), dict):
            raise ContractViolation("invalid checksum manifest")
        for relative, expected in document["files"].items():
            candidate = (self.path / str(relative)).resolve()
            if candidate.parent != self.path and self.path not in candidate.parents:
                raise ContractViolation("checksum manifest contains an unsafe path")
            if not candidate.is_file():
                raise ContractViolation(f"checksummed file is missing: {relative}")
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if actual != expected:
                raise ContractViolation(f"checksum mismatch: {relative}")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ContractViolation(f"expected JSON object: {path.name}")
        return value
