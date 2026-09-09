from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from uga.core.artifact_limits import (
    DEFAULT_ARTIFACT_LIMITS,
    ArtifactResourceLimits,
    parse_json_text,
    read_text_limited,
)
from uga.core.errors import ContractViolation
from uga.recording.replay import ReplayEngine

_SUPPORTED_ACTIONS = frozenset({"moveTo", "click", "write", "press", "scroll", "terminate"})


@dataclass(frozen=True, slots=True)
class OpenCuaAction:
    action_type: str
    params: dict[str, object]
    metadata: dict[str, object]

    def __post_init__(self) -> None:
        if self.action_type not in _SUPPORTED_ACTIONS:
            raise ContractViolation(f"unsupported OpenCUA action: {self.action_type}")
        position = self.params.get("position")
        if self.action_type in {"moveTo", "click"}:
            if not isinstance(position, dict):
                raise ContractViolation("OpenCUA coordinate action requires a position")
            try:
                x = float(position["x"])
                y = float(position["y"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ContractViolation("OpenCUA position is invalid") from exc
            if any(not math.isfinite(value) or not 0 <= value <= 1 for value in (x, y)):
                raise ContractViolation("OpenCUA coordinates must be normalized to [0, 1]")

    def to_dict(self) -> dict[str, object]:
        return {"type": self.action_type, "params": self.params, "metadata": self.metadata}


@dataclass(frozen=True, slots=True)
class OpenCuaStep:
    image: str
    ground_truth_actions: tuple[OpenCuaAction, ...]

    def __post_init__(self) -> None:
        path = PurePosixPath(self.image)
        if (
            not self.image.strip()
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in self.image
            or not self.ground_truth_actions
        ):
            raise ContractViolation("OpenCUA step image/actions are invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "image": self.image,
            "ground_truth_actions": tuple(action.to_dict() for action in self.ground_truth_actions),
        }


@dataclass(frozen=True, slots=True)
class OpenCuaTrajectory:
    task_id: str
    high_level_task_description: str
    steps: tuple[OpenCuaStep, ...]

    def __post_init__(self) -> None:
        if (
            not self.task_id.strip()
            or not self.high_level_task_description.strip()
            or not self.steps
        ):
            raise ContractViolation("OpenCUA trajectory is incomplete")

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "high_level_task_description": self.high_level_task_description,
            "steps": tuple(step.to_dict() for step in self.steps),
        }


class OpenCuaExporter:
    """Exports GUI-compatible UGA physical actions to AgentNetBench schema."""

    def export(self, episode_path: str | Path) -> OpenCuaTrajectory:
        replay = ReplayEngine(episode_path)
        action_order = {
            str(row["reference_id"]): int(row["sequence"])
            for row in replay.timeline
            if row["kind"] in {"action", "raw_input"}
        }
        actions_by_observation: dict[str, list[dict[str, object]]] = {}
        for action in replay.actions:
            observation_id = action.get("observation_id")
            if observation_id is None or action.get("action_layer") != "physical":
                continue
            actions_by_observation.setdefault(str(observation_id), []).append(action)
        for actions in actions_by_observation.values():
            actions.sort(key=lambda action: action_order.get(str(action["action_id"]), 2**63 - 1))
        steps: list[OpenCuaStep] = []
        for observation in replay.observations:
            observation_id = str(observation["observation_id"])
            actions = actions_by_observation.get(observation_id, [])
            if not actions:
                continue
            payload: Any = parse_json_text(str(observation["payload_json"]))
            if not isinstance(payload, dict):
                raise ContractViolation("OpenCUA export observation payload must be an object")
            image = self._image_path(payload, replay.path)
            rect = self._client_rect(payload)
            mapped = self._map_actions(actions, rect)
            if mapped:
                steps.append(OpenCuaStep(image, mapped))
        if not steps:
            raise ContractViolation("Episode contains no OpenCUA-compatible GUI steps")
        return OpenCuaTrajectory(
            str(replay.metadata["episode_id"]),
            str(replay.metadata["task"]),
            tuple(steps),
        )

    @staticmethod
    def _image_path(payload: dict[str, object], episode_root: Path) -> str:
        image = next(
            (
                value
                for key in ("image", "image_path", "frame_path")
                if isinstance((value := payload.get(key)), str) and value.strip()
            ),
            None,
        )
        if image is None:
            raise ContractViolation("OpenCUA export requires an observation image path")
        path = PurePosixPath(image)
        candidate = (episode_root / image).resolve()
        if path.is_absolute() or ".." in path.parts or episode_root not in candidate.parents:
            raise ContractViolation("OpenCUA observation image path is unsafe")
        if not candidate.is_file():
            raise ContractViolation(f"OpenCUA observation image is missing: {image}")
        return image

    @staticmethod
    def _client_rect(payload: dict[str, object]) -> tuple[float, float, float, float]:
        raw = payload.get("client_screen_rect")
        if not isinstance(raw, dict):
            raise ContractViolation("OpenCUA export requires client_screen_rect")
        try:
            rect = tuple(float(raw[key]) for key in ("left", "top", "right", "bottom"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractViolation("OpenCUA client_screen_rect is invalid") from exc
        left, top, right, bottom = rect
        if not all(math.isfinite(value) for value in rect) or right <= left or bottom <= top:
            raise ContractViolation("OpenCUA client_screen_rect has invalid geometry")
        return left, top, right, bottom

    @staticmethod
    def _map_actions(
        actions: list[dict[str, object]], rect: tuple[float, float, float, float]
    ) -> tuple[OpenCuaAction, ...]:
        left, top, right, bottom = rect
        position: dict[str, float] | None = None
        result: list[OpenCuaAction] = []
        for action in actions:
            action_type = str(action["action_type"])
            payload: Any = parse_json_text(str(action["payload_json"]))
            if not isinstance(payload, dict):
                raise ContractViolation("recorded action payload must be an object")
            metadata: dict[str, object] = {
                "bboxes": (),
                "uga_action_id": str(action["action_id"]),
                "uga_action_layer": str(action["action_layer"]),
            }
            if action_type == "AbsolutePointerAction":
                position = {
                    "x": min(max((float(payload["x"]) - left) / (right - left), 0.0), 1.0),
                    "y": min(max((float(payload["y"]) - top) / (bottom - top), 0.0), 1.0),
                }
                result.append(OpenCuaAction("moveTo", {"position": position}, metadata))
            elif action_type == "MouseButtonAction":
                if bool(payload.get("is_down")):
                    if position is None:
                        raise ContractViolation("OpenCUA click has no preceding pointer position")
                    result.append(
                        OpenCuaAction(
                            "click",
                            {"position": position, "button": str(payload.get("button", "left"))},
                            metadata,
                        )
                    )
            elif action_type == "UnicodeTextAction":
                result.append(OpenCuaAction("write", {"text": str(payload["text"])}, metadata))
            elif action_type == "KeyboardAction" and bool(payload.get("is_down")):
                result.append(
                    OpenCuaAction(
                        "press",
                        {
                            "key_code": int(payload["code"]),
                            "encoding": str(payload.get("encoding", "scan_code")),
                        },
                        metadata,
                    )
                )
            elif action_type == "WheelAction":
                result.append(OpenCuaAction("scroll", {"delta": int(payload["delta"])}, metadata))
            elif action_type in {"RelativeMouseAction", "GamepadAction", "RawInputAction"}:
                raise ContractViolation(
                    f"{action_type} cannot be represented in OpenCUA GUI coordinates"
                )
        return tuple(result)


def write_opencua_trajectory(trajectory: OpenCuaTrajectory, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(trajectory.to_dict(), indent=2) + "\n", encoding="utf-8")
    return destination


def load_opencua_trajectory(
    path: str | Path,
    *,
    limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
) -> OpenCuaTrajectory:
    try:
        payload: Any = parse_json_text(
            read_text_limited(
                path,
                limits.max_document_bytes,
                "OpenCUA trajectory",
            )
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("steps"), list):
            raise ContractViolation("OpenCUA trajectory root is invalid")
        if len(payload["steps"]) > limits.max_parquet_rows:
            raise ContractViolation("OpenCUA trajectory exceeds the step resource limit")
        steps: list[OpenCuaStep] = []
        for raw_step in payload["steps"]:
            if not isinstance(raw_step, dict) or not isinstance(
                raw_step.get("ground_truth_actions"), list
            ):
                raise ContractViolation("OpenCUA step is invalid")
            if len(raw_step["ground_truth_actions"]) > limits.max_parquet_rows:
                raise ContractViolation("OpenCUA step exceeds the action resource limit")
            actions = tuple(_action_from_dict(item) for item in raw_step["ground_truth_actions"])
            steps.append(OpenCuaStep(str(raw_step["image"]), actions))
        return OpenCuaTrajectory(
            str(payload["task_id"]),
            str(payload["high_level_task_description"]),
            tuple(steps),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractViolation(f"invalid OpenCUA trajectory: {exc}") from exc


def _action_from_dict(payload: object) -> OpenCuaAction:
    if not isinstance(payload, dict):
        raise ContractViolation("OpenCUA action must be an object")
    params = payload.get("params", {})
    metadata = payload.get("metadata", {})
    if not isinstance(params, dict) or not isinstance(metadata, dict):
        raise ContractViolation("OpenCUA action params/metadata must be objects")
    return OpenCuaAction(str(payload.get("type", "")), params, metadata)
