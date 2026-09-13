from __future__ import annotations

import hashlib
import importlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from uga.capture.frame import BufferHandle, BufferKind, Frame, PixelFormat
from uga.capture.ring_buffer import SequencedFrame
from uga.control.lease import ControlMode
from uga.core.artifact_limits import DEFAULT_ARTIFACT_LIMITS, iter_text_lines_limited
from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.evaluation.grounding_qualification import load_grounding_corpus
from uga.perception.builder import PerceptionBuilder
from uga.perception.schema import PerceptionSnapshot, PlannerOutcome
from uga.release.revision import validate_source_revision
from uga.time.clock import UGATime
from uga.windows.coordinates import Rect
from uga.windows.window_identity import WindowIdentity


class OfflineGroundedPlanner(Protocol):
    @property
    def last_schema_valid(self) -> bool | None: ...

    @property
    def last_raw_reply(self) -> str | None: ...

    def decide(
        self,
        *,
        snapshot: PerceptionSnapshot,
        frames: tuple[Frame, ...],
        goal: str,
        high_resolution_retry: bool = False,
    ) -> PlannerOutcome: ...


class OfflinePerceptionBuilder(Protocol):
    def build(
        self,
        item: SequencedFrame,
        mode: ControlMode,
        *,
        geometry_generation: int,
        task_generation: int,
        goal_facts: tuple[tuple[str, str], ...] = (),
    ) -> PerceptionSnapshot: ...


def _load_frame(path: Path, *, frame_id: str, sequence: int) -> Frame:
    try:
        image_module = importlib.import_module("PIL.Image")
    except ImportError as exc:
        raise BackendUnavailableError("offline grounding requires Pillow") from exc
    try:
        with image_module.open(path) as source:
            image = source.convert("RGBA")
            width, height = image.size
            payload = image.tobytes("raw", "BGRA")
    except (OSError, ValueError) as exc:
        raise ContractViolation(f"cannot decode grounding frame: {path}") from exc
    identity = WindowIdentity(
        1,
        1,
        "f" * 64,
        1,
        1,
    )
    timestamp = UGATime(sequence * 1_000_000_000)
    return Frame(
        frame_id,
        timestamp,
        None,
        identity,
        width,
        height,
        width * 4,
        PixelFormat.BGRA8,
        Rect(0, 0, width, height),
        Rect(0, 0, width, height),
        "offline_fixture",
        BufferHandle(
            f"offline:{frame_id}",
            BufferKind.CPU_BYTES,
            len(payload),
            payload,
        ),
    )


def _completed_predictions(
    output: Path, *, source_revision: str, model_id: str
) -> set[str]:
    if not output.exists():
        return set()
    completed: set[str] = set()
    for line_number, line in iter_text_lines_limited(
        output,
        maximum_bytes=DEFAULT_ARTIFACT_LIMITS.max_benchmark_jsonl_bytes,
        maximum_line_bytes=DEFAULT_ARTIFACT_LIMITS.max_benchmark_line_bytes,
        label="grounding predictions JSONL",
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractViolation(
                f"invalid resumable prediction line {line_number}: {exc}"
            ) from exc
        if (
            not isinstance(row, dict)
            or row.get("source_revision") != source_revision
            or row.get("model_id") != model_id
            or not isinstance(row.get("sample_id"), str)
        ):
            raise ContractViolation("existing predictions do not match this source/model run")
        sample_id = str(row["sample_id"])
        if sample_id in completed:
            raise ContractViolation(f"duplicate resumable prediction id: {sample_id}")
        completed.add(sample_id)
    return completed


def run_grounding_predictions(
    *,
    annotations_path: str | Path,
    output_path: str | Path,
    source_revision: str,
    model_id: str,
    planner: OfflineGroundedPlanner,
    perception_builder: OfflinePerceptionBuilder,
    require_qualification_volume: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
) -> Path:
    validate_source_revision(source_revision)
    if not model_id.strip():
        raise ContractViolation("offline grounding model id cannot be blank")
    corpus = load_grounding_corpus(
        annotations_path,
        require_qualification_volume=require_qualification_volume,
    )
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = _completed_predictions(
        output,
        source_revision=source_revision,
        model_id=model_id,
    )
    unknown = completed - set(corpus.sample_ids)
    if unknown:
        raise ContractViolation(
            f"existing predictions contain unknown samples: {sorted(unknown)[:3]}"
        )
    with output.open("a", encoding="utf-8", newline="\n") as stream:
        for index, item in enumerate(corpus.items, 1):
            if item.sample.sample_id in completed:
                continue
            frames = tuple(
                _load_frame(
                    path,
                    frame_id=f"{item.sample.sample_id}-{frame_index}",
                    sequence=frame_index,
                )
                for frame_index, path in enumerate(item.frame_paths, 1)
            )
            latest = SequencedFrame(len(frames), frames[-1])
            snapshot = perception_builder.build(
                latest,
                ControlMode.GUI,
                geometry_generation=1,
                task_generation=1,
            )
            started = time.monotonic()
            outcome = planner.decide(
                snapshot=snapshot,
                frames=frames,
                goal=item.goal,
            )
            latency = time.monotonic() - started
            action = outcome.action
            raw = planner.last_raw_reply
            row = {
                "sample_id": item.sample.sample_id,
                "source_revision": source_revision,
                "model_id": model_id,
                "schema_valid": planner.last_schema_valid is True,
                "kind": outcome.kind.value,
                "visible_text": list(snapshot.text),
                "action_kind": None if action is None else action.kind.value,
                "target_bbox": (
                    None
                    if action is None or action.target_box is None
                    else [
                        action.target_box.left,
                        action.target_box.top,
                        action.target_box.right,
                        action.target_box.bottom,
                    ]
                ),
                "wrong_window": False,
                "latency_seconds": latency,
                "raw_reply_sha256": (
                    None
                    if raw is None
                    else hashlib.sha256(raw.encode("utf-8")).hexdigest()
                ),
            }
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            if progress is not None:
                progress(index, len(corpus.items), item.sample.sample_id)
    return output


def default_perception_builder() -> PerceptionBuilder:
    from uga.perception.text import RapidOcrProvider

    return PerceptionBuilder(RapidOcrProvider())
