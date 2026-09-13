from __future__ import annotations

import math
import os
import queue
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from uga.capture.frame import Frame
from uga.control.canonical import CanonicalAction
from uga.control.physical import PhysicalAction
from uga.control.semantic import SemanticAction
from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    ArtifactResourceLimits,
    sha256_file_limited,
)
from uga.core.errors import ContractViolation
from uga.recording.json_codec import canonical_json, to_json_value, write_json
from uga.recording.parquet_io import write_rows
from uga.recording.schema import (
    ActionProvenance,
    EpisodeMetadata,
    EpisodeResult,
    InputStateRecord,
    RecordedActionLayer,
    TimelineKind,
    TimelineRecord,
)
from uga.recording.video import VideoSink
from uga.time.clock import UGATime

_TIMELINE_SCHEMA = (
    ("sequence", "int64"),
    ("timestamp_ns", "int64"),
    ("elapsed_ns", "int64"),
    ("kind", "string"),
    ("reference_id", "string"),
    ("payload_json", "string"),
)
_ACTION_SCHEMA = (
    ("action_id", "string"),
    ("timestamp_ns", "int64"),
    ("effective_from_ns", "int64"),
    ("expires_at_ns", "int64"),
    ("category", "string"),
    ("action_layer", "string"),
    ("action_type", "string"),
    ("observation_id", "string"),
    ("payload_json", "string"),
    ("input_state_json", "string"),
)
_OBSERVATION_SCHEMA = (
    ("observation_id", "string"),
    ("timestamp_ns", "int64"),
    ("payload_json", "string"),
)
_PROVENANCE_SCHEMA = (
    ("action_id", "string"),
    ("action_source", "string"),
    ("policy_version", "string"),
    ("model_checkpoint", "string"),
    ("observation_id", "string"),
    ("skill_id", "string"),
    ("task_node_id", "string"),
    ("mode", "string"),
    ("lease_id", "string"),
    ("confidence", "float64"),
    ("human_override", "bool"),
    ("created_at_ns", "int64"),
    ("effective_from_ns", "int64"),
    ("expires_at_ns", "int64"),
)

_PUBLISH_RETRY_ATTEMPTS = 20
_PUBLISH_RETRY_DELAY_S = 0.05


class EpisodeWriter:
    """Transactional writer for one immutable UGA V1.1 episode."""

    def __init__(
        self,
        root: str | Path,
        metadata: EpisodeMetadata,
        *,
        require_video: bool = True,
        limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
    ) -> None:
        if metadata.result != EpisodeResult.IN_PROGRESS or metadata.end_monotonic_ns is not None:
            raise ContractViolation("new episode metadata must be in progress with no end time")
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._final_path = (self._root / metadata.episode_id).resolve()
        if self._final_path.parent != self._root:
            raise ContractViolation("episode id must be a single safe path component")
        if self._final_path.exists():
            raise FileExistsError(self._final_path)
        self._staging_path = (
            self._root / f".{metadata.episode_id}.inprogress-{uuid.uuid4().hex}"
        ).resolve()
        self._staging_path.mkdir()
        (self._staging_path / "thumbnails").mkdir()
        self._metadata = metadata
        self._require_video = require_video
        self._limits = limits
        self._video: VideoSink | None = None
        self._timeline: list[dict[str, Any]] = []
        self._actions: list[dict[str, Any]] = []
        self._observations: list[dict[str, Any]] = []
        self._provenance: list[dict[str, Any]] = []
        self._tasks: list[dict[str, Any]] = []
        self._planner: list[dict[str, Any]] = []
        self._metrics: dict[str, float | int] = {}
        self._events: list[dict[str, Any]] = []
        self._annotations: list[dict[str, Any]] = []
        self._action_ids: set[str] = set()
        self._observation_ids: set[str] = set()
        self._sequence = 0
        self._buffer_rows = 0
        self._buffer_bytes = 0
        self._metrics_bytes = 0
        self._latest_timestamp_ns = metadata.start_monotonic_ns
        self._closed = False
        self._lock = Lock()

    @property
    def staging_path(self) -> Path:
        return self._staging_path

    @property
    def video_path(self) -> Path:
        return self._staging_path / "video.mp4"

    def attach_video(self, video: VideoSink) -> None:
        with self._lock:
            self._ensure_open()
            if self._video is not None:
                raise ContractViolation("episode already has a video sink")
            self._video = video

    def set_terminal_context(
        self, termination_reason: str, goal_confidence: float | None = None
    ) -> None:
        if not termination_reason.strip():
            raise ContractViolation("episode termination reason cannot be blank")
        if goal_confidence is not None and not 0.0 <= goal_confidence <= 1.0:
            raise ContractViolation("episode goal confidence must be in [0, 1]")
        with self._lock:
            self._ensure_open()
            self._metadata = replace(
                self._metadata,
                termination_reason=termination_reason,
                goal_confidence=goal_confidence,
            )

    def record_frame(self, frame: Frame) -> None:
        with self._lock:
            self._ensure_open()
            self._validate_timestamp(frame.capture_timestamp)
            if self._video is None and self._require_video:
                raise ContractViolation("attach a video sink before recording frames")
            envelope = frame.to_envelope()
            self._ensure_table_capacity(len(self._timeline), 1, "timeline")
            self._reserve_buffer(1, envelope)
            if self._video is not None:
                self._video.append(frame)
            self._append_timeline(
                frame.capture_timestamp,
                TimelineKind.FRAME,
                frame.frame_id,
                envelope,
            )

    def record_observation(self, observation_id: str, timestamp: UGATime, payload: object) -> None:
        if not observation_id.strip():
            raise ContractViolation("observation id cannot be blank")
        with self._lock:
            self._ensure_open()
            self._validate_timestamp(timestamp)
            if observation_id in self._observation_ids:
                raise ContractViolation(f"duplicate observation id: {observation_id}")
            observation = {
                "observation_id": observation_id,
                "timestamp_ns": timestamp.value_ns,
                "payload_json": canonical_json(payload),
            }
            self._ensure_table_capacity(len(self._observations), 1, "observations")
            self._ensure_table_capacity(len(self._timeline), 1, "timeline")
            self._reserve_buffer(2, observation, payload)
            self._observation_ids.add(observation_id)
            self._observations.append(observation)
            self._append_timeline(timestamp, TimelineKind.OBSERVATION, observation_id, payload)

    def record_action(
        self,
        action: PhysicalAction,
        provenance: ActionProvenance,
        *,
        input_state: InputStateRecord | None = None,
    ) -> None:
        self._record_action(action, provenance, RecordedActionLayer.PHYSICAL, input_state)

    def record_canonical_action(
        self, action: CanonicalAction, provenance: ActionProvenance
    ) -> None:
        self._record_action(action, provenance, RecordedActionLayer.CANONICAL, None)

    def record_semantic_action(self, action: SemanticAction, provenance: ActionProvenance) -> None:
        self._record_action(action, provenance, RecordedActionLayer.SEMANTIC, None)

    def _record_action(
        self,
        action: PhysicalAction | CanonicalAction | SemanticAction,
        provenance: ActionProvenance,
        layer: RecordedActionLayer,
        input_state: InputStateRecord | None,
    ) -> None:
        action.validate()
        provenance.validate()
        if action.action_id != provenance.action_id or action.lifetime != provenance.lifetime:
            raise ContractViolation("action and provenance identity/lifetime must match")
        if input_state is not None and input_state.action != action:
            raise ContractViolation("input-state snapshot must refer to the recorded action")
        if input_state is not None and input_state.timestamp != action.lifetime.created_at:
            raise ContractViolation("input-state snapshot timestamp must match action creation")
        with self._lock:
            self._ensure_open()
            timestamp = action.lifetime.created_at
            self._validate_timestamp(timestamp)
            if action.action_id in self._action_ids:
                raise ContractViolation(f"duplicate action id: {action.action_id}")
            category = "raw_input" if input_state is not None else "agent_action"
            action_row = {
                "action_id": action.action_id,
                "timestamp_ns": timestamp.value_ns,
                "effective_from_ns": action.lifetime.effective_from.value_ns,
                "expires_at_ns": action.lifetime.expires_at.value_ns,
                "category": category,
                "action_layer": layer.value,
                "action_type": type(action).__name__,
                "observation_id": provenance.observation_id,
                "payload_json": canonical_json(action),
                "input_state_json": (None if input_state is None else canonical_json(input_state)),
            }
            provenance_row = {
                "action_id": provenance.action_id,
                "action_source": provenance.action_source,
                "policy_version": provenance.policy_version,
                "model_checkpoint": provenance.model_checkpoint,
                "observation_id": provenance.observation_id,
                "skill_id": provenance.skill_id,
                "task_node_id": provenance.task_node_id,
                "mode": provenance.mode,
                "lease_id": provenance.lease_id,
                "confidence": provenance.confidence,
                "human_override": provenance.human_override,
                "created_at_ns": provenance.lifetime.created_at.value_ns,
                "effective_from_ns": provenance.lifetime.effective_from.value_ns,
                "expires_at_ns": provenance.lifetime.expires_at.value_ns,
            }
            timeline_payload = {
                "action_type": type(action).__name__,
                "source": provenance.action_source,
            }
            self._ensure_table_capacity(len(self._actions), 1, "actions")
            self._ensure_table_capacity(len(self._provenance), 1, "provenance")
            self._ensure_table_capacity(len(self._timeline), 1, "timeline")
            self._reserve_buffer(3, action_row, provenance_row, timeline_payload)
            self._action_ids.add(action.action_id)
            self._actions.append(action_row)
            self._provenance.append(provenance_row)
            self._append_timeline(
                timestamp,
                TimelineKind.RAW_INPUT if input_state is not None else TimelineKind.ACTION,
                action.action_id,
                timeline_payload,
            )

    def record_event(self, event_id: str, timestamp: UGATime, payload: object) -> None:
        self._record_jsonl(TimelineKind.EVENT, event_id, timestamp, payload, self._events)

    def record_annotation(self, annotation_id: str, timestamp: UGATime, payload: object) -> None:
        self._record_jsonl(
            TimelineKind.ANNOTATION,
            annotation_id,
            timestamp,
            payload,
            self._annotations,
        )

    def record_task(self, task_id: str, timestamp: UGATime, payload: object) -> None:
        if not task_id.strip():
            raise ContractViolation("task id cannot be blank")
        with self._lock:
            self._ensure_open()
            self._validate_timestamp(timestamp)
            row = {"task_id": task_id, "timestamp_ns": timestamp.value_ns, "payload": payload}
            self._ensure_table_capacity(len(self._tasks), 1, "tasks")
            self._ensure_table_capacity(len(self._timeline), 1, "timeline")
            self._reserve_buffer(2, row, payload)
            self._tasks.append(row)
            self._append_timeline(timestamp, TimelineKind.TASK, task_id, payload)

    def record_planner(self, decision_id: str, timestamp: UGATime, payload: object) -> None:
        self._record_jsonl(
            TimelineKind.PLANNER,
            decision_id,
            timestamp,
            payload,
            self._planner,
        )

    def set_metrics(self, metrics: dict[str, float | int]) -> None:
        if not metrics or any(not key.strip() for key in metrics):
            raise ContractViolation("recorder metrics require non-blank names")
        if any(
            isinstance(value, bool) or not math.isfinite(float(value)) for value in metrics.values()
        ):
            raise ContractViolation("recorder metrics must be finite numbers")
        with self._lock:
            self._ensure_open()
            metrics_bytes = len(canonical_json(metrics).encode("utf-8"))
            if metrics_bytes > self._limits.max_document_bytes:
                raise ContractViolation("recorder metrics exceed the document resource limit")
            if (
                self._buffer_bytes - self._metrics_bytes + metrics_bytes
                > self._limits.max_episode_buffer_bytes
            ):
                raise ContractViolation("Episode recorder exceeds the buffer resource limit")
            self._buffer_bytes += metrics_bytes - self._metrics_bytes
            self._metrics_bytes = metrics_bytes
            self._metrics = dict(metrics)

    def finalize(self, result: EpisodeResult, end: UGATime | None = None) -> Path:
        if result == EpisodeResult.IN_PROGRESS:
            raise ContractViolation("a finalized episode cannot remain in progress")
        with self._lock:
            self._ensure_open()
            ending = end or UGATime(self._latest_timestamp_ns)
            self._validate_timestamp(ending)
            if self._video is not None:
                self._video.close()
            if self._require_video and not self.video_path.is_file():
                raise ContractViolation("required episode video was not produced")
            self._write_tables()
            self._write_json_files(result, ending)
            write_json(self._staging_path / "checksum.json", self._checksums())
            self._publish_staging()
            self._closed = True
            return self._final_path

    def _publish_staging(self) -> None:
        """Atomically publish after bounded retries for transient Windows locks."""
        for attempt in range(_PUBLISH_RETRY_ATTEMPTS):
            try:
                os.replace(self._staging_path, self._final_path)
                return
            except PermissionError:
                if self._final_path.exists():
                    raise FileExistsError(self._final_path) from None
                if attempt + 1 >= _PUBLISH_RETRY_ATTEMPTS:
                    raise
                time.sleep(_PUBLISH_RETRY_DELAY_S)
        raise AssertionError("unreachable Episode publish retry state")

    def abort(self) -> None:
        """Close attached resources and remove this writer's private staging tree."""
        with self._lock:
            if self._closed:
                return
            staging_prefix = f".{self._metadata.episode_id}.inprogress-"
            if (
                self._staging_path.parent != self._root
                or not self._staging_path.name.startswith(staging_prefix)
            ):
                raise ContractViolation("Episode staging path failed its ownership guard")
            failure: BaseException | None = None
            if self._video is not None:
                try:
                    self._video.close()
                except BaseException as exc:
                    failure = exc
            try:
                if self._staging_path.exists():
                    shutil.rmtree(self._staging_path)
            except BaseException as exc:
                if failure is None:
                    failure = exc
                else:
                    failure.add_note(f"Episode staging cleanup also failed: {exc}")
            self._closed = not self._staging_path.exists()
            if failure is not None:
                raise RuntimeError("failed to abort Episode writer cleanly") from failure

    def _record_jsonl(
        self,
        kind: TimelineKind,
        record_id: str,
        timestamp: UGATime,
        payload: object,
        destination: list[dict[str, Any]],
    ) -> None:
        if not record_id.strip():
            raise ContractViolation(f"{kind} id cannot be blank")
        with self._lock:
            self._ensure_open()
            self._validate_timestamp(timestamp)
            row = {
                "id": record_id,
                "timestamp_ns": timestamp.value_ns,
                "payload": to_json_value(payload),
            }
            self._ensure_table_capacity(len(destination), 1, kind.value)
            self._ensure_table_capacity(len(self._timeline), 1, "timeline")
            self._reserve_buffer(2, row, payload)
            destination.append(row)
            self._append_timeline(timestamp, kind, record_id, payload)

    def _append_timeline(
        self, timestamp: UGATime, kind: TimelineKind, reference_id: str, payload: object
    ) -> None:
        self._sequence += 1
        record = TimelineRecord(
            self._sequence,
            timestamp.value_ns,
            timestamp.value_ns - self._metadata.start_monotonic_ns,
            kind,
            reference_id,
            canonical_json(payload),
        )
        self._timeline.append(
            {
                "sequence": record.sequence,
                "timestamp_ns": record.timestamp_ns,
                "elapsed_ns": record.elapsed_ns,
                "kind": record.kind.value,
                "reference_id": record.reference_id,
                "payload_json": record.payload_json,
            }
        )

    def _ensure_table_capacity(self, current: int, additional: int, label: str) -> None:
        if current + additional > self._limits.max_parquet_rows:
            raise ContractViolation(f"Episode {label} exceeds the row resource limit")

    def _reserve_buffer(self, rows: int, *payloads: object) -> None:
        sizes = tuple(len(canonical_json(payload).encode("utf-8")) for payload in payloads)
        if any(size > self._limits.max_jsonl_line_bytes for size in sizes):
            raise ContractViolation("Episode record exceeds the per-record resource limit")
        added_bytes = sum(sizes)
        if self._buffer_rows + rows > self._limits.max_parquet_rows * 4:
            raise ContractViolation("Episode recorder exceeds the record resource limit")
        if self._buffer_bytes + added_bytes > self._limits.max_episode_buffer_bytes:
            raise ContractViolation("Episode recorder exceeds the buffer resource limit")
        self._buffer_rows += rows
        self._buffer_bytes += added_bytes

    def _validate_timestamp(self, timestamp: UGATime) -> None:
        if timestamp.value_ns < self._metadata.start_monotonic_ns:
            raise ContractViolation("record timestamp precedes episode start")
        self._latest_timestamp_ns = max(self._latest_timestamp_ns, timestamp.value_ns)

    def _write_tables(self) -> None:
        timeline = sorted(self._timeline, key=lambda row: (row["timestamp_ns"], row["sequence"]))
        actions = sorted(self._actions, key=lambda row: (row["timestamp_ns"], row["action_id"]))
        observations = sorted(
            self._observations,
            key=lambda row: (row["timestamp_ns"], row["observation_id"]),
        )
        provenance = sorted(self._provenance, key=lambda row: row["action_id"])
        write_rows(self._staging_path / "timeline.parquet", timeline, _TIMELINE_SCHEMA)
        write_rows(self._staging_path / "actions.parquet", actions, _ACTION_SCHEMA)
        write_rows(
            self._staging_path / "observations.parquet",
            observations,
            _OBSERVATION_SCHEMA,
        )
        write_rows(
            self._staging_path / "provenance.parquet",
            provenance,
            _PROVENANCE_SCHEMA,
        )

    def _write_json_files(self, result: EpisodeResult, end: UGATime) -> None:
        metadata = replace(self._metadata, result=result, end_monotonic_ns=end.value_ns)
        metadata_payload = to_json_value(metadata)
        assert isinstance(metadata_payload, dict)
        write_json(
            self._staging_path / "metadata.json",
            {
                "schema": metadata.SCHEMA_NAME,
                "schema_version": metadata.SCHEMA_VERSION,
                **metadata_payload,
            },
        )
        write_json(self._staging_path / "tasks.json", self._tasks)
        write_json(
            self._staging_path / "run.json",
            {
                "schema": "uga.run",
                "schema_version": "1.1",
                "run_id": metadata.episode_id,
                "game_id": metadata.game_id,
                "start_monotonic_ns": metadata.start_monotonic_ns,
                "end_monotonic_ns": metadata.end_monotonic_ns,
                "result": metadata.result.value,
                "agent_version": metadata.agent_version,
                "policy_version": metadata.policy_version,
                "termination_reason": metadata.termination_reason,
                "goal_confidence": metadata.goal_confidence,
                "source_revision": metadata.source_revision,
                "source_tree_clean": metadata.source_tree_clean,
                "model_id": metadata.model_id,
            },
        )
        self._write_jsonl(self._staging_path / "events.jsonl", self._events)
        self._write_jsonl(self._staging_path / "annotations.jsonl", self._annotations)
        self._write_jsonl(self._staging_path / "planner.jsonl", self._planner)
        write_json(self._staging_path / "metrics.json", self._metrics)

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(canonical_json(row) + "\n")

    def _checksums(self) -> dict[str, object]:
        files: dict[str, str] = {}
        total_bytes = 0
        for path in sorted(self._staging_path.rglob("*")):
            if path.is_file() and path != self._staging_path / "checksum.json":
                if len(files) >= self._limits.max_episode_files - 1:
                    raise ContractViolation("Episode exceeds the file-count resource limit")
                relative = path.relative_to(self._staging_path).as_posix()
                try:
                    total_bytes += path.stat().st_size
                except OSError as exc:
                    raise ContractViolation(f"cannot inspect Episode file: {path}") from exc
                if total_bytes > self._limits.max_episode_bytes:
                    raise ContractViolation("Episode exceeds the total byte resource limit")
                maximum = (
                    self._limits.max_video_bytes
                    if path.name == "video.mp4"
                    else self._limits.max_parquet_file_bytes
                    if path.suffix == ".parquet"
                    else self._limits.max_jsonl_bytes
                    if path.suffix == ".jsonl"
                    else self._limits.max_document_bytes
                )
                files[relative] = sha256_file_limited(path, maximum, f"Episode file {relative}")
        return {"algorithm": "sha256", "files": files}

    def _ensure_open(self) -> None:
        if self._closed:
            raise ContractViolation("episode writer is already finalized")


class RecorderChannel:
    """Dedicated bounded channel that blocks producers instead of dropping records."""

    def __init__(self, capacity: int = 1024) -> None:
        if capacity < 1:
            raise ContractViolation("recorder channel capacity must be positive")
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue(capacity)
        self._failure: BaseException | None = None
        self._closed = False
        self._state_lock = Lock()
        self._thread = Thread(target=self._run, name="uga-recorder", daemon=True)
        self._thread.start()

    def submit(self, operation: Callable[[], None], timeout_s: float | None = None) -> None:
        with self._state_lock:
            if self._closed:
                raise ContractViolation("recorder channel is closed")
            if self._failure is not None:
                raise RuntimeError("recorder worker failed") from self._failure
            try:
                self._queue.put(operation, block=True, timeout=timeout_s)
            except queue.Full as exc:
                raise TimeoutError(
                    "recorder channel backpressure timeout; record was not dropped"
                ) from exc

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(None)
        self._thread.join()
        if self._failure is not None:
            raise RuntimeError("recorder worker failed") from self._failure

    def _run(self) -> None:
        while True:
            operation = self._queue.get()
            try:
                if operation is None:
                    return
                if self._failure is None:
                    operation()
            except BaseException as exc:
                if self._failure is None:
                    self._failure = exc
            finally:
                self._queue.task_done()
