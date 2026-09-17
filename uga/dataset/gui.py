"""Atomic receipt-backed GUI labels and recorded pre-action video frames.

MP4 images are lossy, never claimed to be lossless original captures. Original
client-normalized action coordinates and video resize geometry remain explicit.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    ArtifactResourceLimits,
    ensure_file_size,
)
from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.dataset.processor import DatasetProcessor
from uga.recording.replay import ReplayEngine


def export_gui_samples(
    episode_path: str | Path,
    output: str | Path,
    *,
    limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
) -> Path:
    replay = ReplayEngine(episode_path, limits=limits)
    processed = DatasetProcessor(limits=limits).process(episode_path)
    samples = [
        s for s in processed.samples if s.action_layer == "gui" or s.action_type == "GuiAction"
    ]
    if not samples or len(samples) > limits.max_training_samples:
        raise ContractViolation("GUI export needs qualified logical actions within sample limits")
    frames = [row for row in replay.timeline if row["kind"] == "frame"]
    index_by_id = {str(row["reference_id"]): i for i, row in enumerate(frames)}
    if len(index_by_id) != len(frames):
        raise ContractViolation("GUI export requires unique frame identities")
    destination, source = Path(output).resolve(), Path(episode_path).resolve()
    if destination == source or source in destination.parents:
        raise ContractViolation("GUI export must not mutate its source Episode")
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        (temporary / "images").mkdir()
        rows: list[dict[str, Any]] = []
        rows_by_index: dict[int, list[dict[str, Any]]] = {}
        json_bytes = 0
        for sample in samples:
            observation = json.loads(sample.observation_json)
            if not isinstance(observation, dict) or observation.get("schema") != "uga.observation":
                raise ContractViolation("GUI export requires an actual frame-backed observation")
            try:
                geometry = observation["data"]["latest_frame"]["data"]
                frame_id = geometry["frame_id"]
                if min(int(geometry["width"]) & ~1, int(geometry["height"]) & ~1) < 2:
                    raise ValueError("frame cannot be represented by video recorder")
            except (KeyError, TypeError, ValueError) as exc:
                raise ContractViolation("GUI observation geometry is invalid") from exc
            if frame_id not in index_by_id:
                raise ContractViolation("GUI pre-action frame absent from recorded video timeline")
            if geometry["capture_timestamp_ns"] != sample.observation_timestamp_ns:
                raise ContractViolation("GUI capture timestamp differs from pre-action binding")
            index = index_by_id[frame_id]
            row = {
                "schema": "uga.gui-training/1",
                "episode_id": sample.episode_id,
                "action_id": sample.action_id,
                "observation_id": sample.observation_id,
                "inference_observation_id": sample.inference_observation_id,
                "image": f"images/{index:08d}.png",
                "frame_id": frame_id,
                "capture_ns": sample.observation_timestamp_ns,
                "executed_at_ns": sample.action_timestamp_ns,
                "coordinate_space": "client_normalized",
                "frame_geometry": geometry,
                "action": json.loads(sample.action_json),
            }
            length = len(json.dumps(row).encode()) + 1
            json_bytes += length
            if length > limits.max_jsonl_line_bytes or json_bytes > limits.max_jsonl_bytes:
                raise ContractViolation("GUI export exceeds JSON resource limits")
            rows.append(row)
            rows_by_index.setdefault(index, []).append(row)
        video = source / "video.mp4"
        ensure_file_size(video, limits.max_video_bytes, "GUI source video")
        try:
            av = importlib.import_module("av")
        except ImportError as exc:
            raise BackendUnavailableError("GUI image export requires av") from exc
        total_bytes = json_bytes
        with av.open(str(video)) as container:
            if len(container.streams.video) != 1:
                raise ContractViolation("GUI source needs exactly one video stream")
            stream = container.streams.video[0]
            _check_geometry(stream.codec_context.width, stream.codec_context.height, limits)
            decoded_count = 0
            for index, decoded in enumerate(container.decode(stream)):
                if index >= limits.max_video_frames:
                    raise ContractViolation("GUI video exceeds frame resource limit")
                _check_geometry(decoded.width, decoded.height, limits)
                decoded_count += 1
                if index not in rows_by_index:
                    continue
                for row in rows_by_index[index]:
                    geometry = row["frame_geometry"]
                    row["image_geometry"] = {
                        "width": decoded.width,
                        "height": decoded.height,
                        "source_crop_width": int(geometry["width"]) & ~1,
                        "source_crop_height": int(geometry["height"]) & ~1,
                        "encoding": "recorded_video_lossy",
                    }
                target = decoded.reformat(format="rgb24")
                codec = av.codec.context.CodecContext.create("png", "w")
                codec.width, codec.height, codec.pix_fmt = target.width, target.height, "rgb24"
                encoded = b"".join(bytes(p) for p in codec.encode(target))
                encoded += b"".join(bytes(p) for p in codec.encode())
                total_bytes += len(encoded)
                if total_bytes > limits.max_dataset_bytes:
                    raise ContractViolation("GUI export exceeds total byte resource limit")
                (temporary / "images" / f"{index:08d}.png").write_bytes(encoded)
            if decoded_count != len(frames):
                raise ContractViolation("GUI video/timeline count mismatch")
        actual_json_bytes = 0
        with (temporary / "samples.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                line = json.dumps(row, ensure_ascii=False) + "\n"
                size = len(line.encode("utf-8"))
                actual_json_bytes += size
                if (
                    size > limits.max_jsonl_line_bytes
                    or actual_json_bytes > limits.max_jsonl_bytes
                    or total_bytes - json_bytes + actual_json_bytes > limits.max_dataset_bytes
                ):
                    raise ContractViolation("GUI export exceeds final byte limits")
                handle.write(line)
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(temporary, destination)
        return destination
    except BaseException:
        shutil.rmtree(temporary)
        raise


def _check_geometry(width: int, height: int, limits: ArtifactResourceLimits) -> None:
    if (
        min(width, height) < 1
        or max(width, height) > limits.max_video_dimension
        or width * height > limits.max_video_pixels
    ):
        raise ContractViolation("GUI video dimensions exceed resource limits")
