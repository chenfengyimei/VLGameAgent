"""Vision-model planner policy: the runtime looks at the screen and decides.

Sends the newest captured frame to an OpenAI-compatible vision endpoint — a
local LM Studio server or any cloud vision API — together with the current
goal, parses a strictly validated JSON action, and emits an ActionChunk with
absolute pointer coordinates derived from fresh window geometry. Coordinates
are never supplied from outside the runtime; the loop is closed: observe →
model decides → tap → observe the result → decide again.

Transport uses the standard library only (urllib); frames are downscaled and
encoded as PNG through the declared PyAV dependency.
"""

from __future__ import annotations

import base64
import importlib
import json
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any

from uga.capture.frame import BufferKind, Frame, PixelFormat
from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.policy.action_chunk import ActionButton, ActionChunk
from uga.policy.fast_policy import FastPolicyOutput, PolicyContext
from uga.time.clock import ClockBackend, PerfCounterClock, UGATime
from uga.windows.coordinates import Rect

MAX_CONSECUTIVE_FAILURES = 5
_SUPPORTED_FORMATS = (PixelFormat.BGRA8, PixelFormat.RGBA8)


class PlannerReplyError(ContractViolation):
    """The vision model reply could not be parsed into a valid action."""


def encode_frame_png(frame: Frame, *, max_width: int = 960) -> bytes:
    """Encode a captured frame as a PNG no wider than ``max_width`` pixels."""
    if frame.pixel_format not in _SUPPORTED_FORMATS:
        raise ContractViolation("vision planner requires BGRA8 or RGBA8 frames")
    if max_width < 32:
        raise ContractViolation("vision planner image width floor is 32 pixels")
    try:
        av = importlib.import_module("av")
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise BackendUnavailableError(
            "vision planner requires the declared av dependency"
        ) from exc
    handle = frame.buffer_handle
    if handle.kind != BufferKind.CPU_BYTES:
        raise ContractViolation("vision planner requires a CPU byte frame")
    source = handle.readonly_view()
    row_bytes = frame.width * 4
    if frame.stride_bytes < row_bytes or source.nbytes < frame.stride_bytes * frame.height:
        raise ContractViolation("vision planner frame buffer is smaller than its stride")
    pixel_format = "bgra" if frame.pixel_format == PixelFormat.BGRA8 else "rgba"
    video_frame = av.VideoFrame(frame.width, frame.height, pixel_format)
    plane = video_frame.planes[0]
    packed = bytearray(plane.buffer_size)
    for row in range(frame.height):
        source_start = row * frame.stride_bytes
        target_start = row * plane.line_size
        packed[target_start : target_start + row_bytes] = source[
            source_start : source_start + row_bytes
        ]
    plane.update(packed)
    target = video_frame.reformat(format="rgb24")
    if frame.width > max_width:
        scaled_height = max(1, round(frame.height * max_width / frame.width))
        target = target.reformat(width=max_width, height=scaled_height)
    codec = av.codec.context.CodecContext.create("png", "w")
    codec.width = target.width
    codec.height = target.height
    codec.pix_fmt = "rgb24"
    encoded = bytearray()
    for packet in codec.encode(target):
        encoded += bytes(packet)
    for packet in codec.encode():
        encoded += bytes(packet)
    if not encoded.startswith(b"\x89PNG"):
        raise ContractViolation("vision planner produced a malformed PNG frame")
    return bytes(encoded)


class OpenAICompatibleVisionClient:
    """Minimal OpenAI-compatible chat client for local or cloud vision APIs."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_s: float = 30.0,
    ) -> None:
        if not base_url.strip() or not model.strip():
            raise ContractViolation("vision client requires a base URL and a model name")
        if timeout_s <= 0:
            raise ContractViolation("vision client timeout must be positive")
        root = base_url.rstrip("/")
        if not root.endswith("/chat/completions"):
            root = root + "/chat/completions"
        self._endpoint = root
        self._model = model
        self._api_key = api_key
        self._timeout_s = timeout_s

    def decide(self, *, image_png: bytes, instruction: str) -> str:
        """Send one frame plus the instruction; return the model's text reply."""
        data_uri = "data:image/png;base64," + base64.b64encode(image_png).decode("ascii")
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": instruction},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": 200,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise BackendUnavailableError(f"vision endpoint returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise BackendUnavailableError(f"vision endpoint unreachable: {exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendUnavailableError("vision reply is missing message content") from exc
        if not isinstance(content, str) or not content.strip():
            raise BackendUnavailableError("vision reply content is empty")
        return content


def parse_planner_reply(reply: str) -> tuple[str, float | None, float | None]:
    """Parse a model reply into ``("tap", x, y)`` or ``("wait", None, None)``.

    Accepts the JSON object with or without markdown fences; anything else is
    a PlannerReplyError so the caller can retry instead of inventing actions.
    """
    text = reply.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise PlannerReplyError("vision reply contains no JSON object")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise PlannerReplyError(f"vision reply is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PlannerReplyError("vision reply JSON is not an object")
    action = payload.get("action")
    if action == "wait":
        return "wait", None, None
    if action != "tap":
        raise PlannerReplyError(f"vision reply has unknown action: {action!r}")
    try:
        x = float(payload["x"])
        y = float(payload["y"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PlannerReplyError("vision tap reply lacks numeric x/y coordinates") from exc
    if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
        raise PlannerReplyError("vision tap coordinates must be fractions within [0, 1]")
    return "tap", x, y


def build_instruction(goal: str, last_action: str | None) -> str:
    """Compose the decision prompt; coordinates are always client fractions."""
    history = (
        ""
        if last_action is None
        else f"上一次动作是 {last_action}。如果画面因此没有变化，请仔细重新观察，尝试别的按钮。\n"
    )
    return (
        "你是一个安卓游戏自动操作智能体。请仔细观察这张游戏截图。\n"
        f"当前任务目标：{goal}。\n"
        f"{history}"
        "坐标以百分比表示：x 为横向 0.0（最左）~1.0（最右），y 为纵向 0.0（最上）~1.0（最下）。\n"
        "回答格式（严格遵守两行）：\n"
        "第一行：用一句话描述画面状态，读出你看到的按钮上的文字。\n"
        "第二行：只输出一个 JSON 对象，点击最能推进任务目标的按钮：\n"
        '{"action":"tap","x":<按钮中心的横向百分比>,"y":<按钮中心的纵向百分比>}\n'
        '或画面在加载、无合适目标时输出：{"action":"wait"}\n'
        "规则：坐标必须在 0~1 之间；优先点击文字与任务目标相关的按钮；"
        "不要编造画面中不存在的元素。\n"
    )


class VlmPlannerPolicy:
    """Perception-driven tap policy backed by a vision-language model."""

    def __init__(
        self,
        *,
        client: OpenAICompatibleVisionClient,
        frame_source: Callable[[], Frame],
        client_rect: Callable[[], Rect],
        goal: str,
        decision_interval_s: float = 6.0,
        failure_backoff_s: float = 10.0,
        policy_version: str = "vlm-planner-v1",
        max_image_width: int = 960,
        clock: ClockBackend | None = None,
    ) -> None:
        if decision_interval_s <= 0.0 or failure_backoff_s <= 0.0:
            raise ContractViolation("vision planner cadence must be positive")
        if not goal.strip():
            raise ContractViolation("vision planner requires a task goal")
        self._client = client
        self._frame_source = frame_source
        self._client_rect = client_rect
        self._goal = goal
        self._decision_interval_s = decision_interval_s
        self._failure_backoff_s = failure_backoff_s
        self._policy_version = policy_version
        self._max_image_width = max_image_width
        self._clock = clock if clock is not None else PerfCounterClock()
        self._next_decision_at = 0.0
        self._failures = 0
        self._last_point: tuple[int, int] | None = None
        self._last_action: str | None = None

    @property
    def policy_version(self) -> str:
        return self._policy_version

    def infer(self, context: PolicyContext) -> FastPolicyOutput:
        now = time.monotonic()
        if now < self._next_decision_at:
            wait_s = max(self._next_decision_at - now, 0.05)
            return self._hold_chunk(context, duration=wait_s)
        try:
            frame = self._frame_source()
            rect = self._client_rect()
            image = encode_frame_png(frame, max_width=self._max_image_width)
            instruction = build_instruction(self._goal, self._last_action)
            reply = self._client.decide(image_png=image, instruction=instruction)
            action, x, y = parse_planner_reply(reply)
        except Exception as exc:
            self._failures += 1
            if self._failures >= MAX_CONSECUTIVE_FAILURES:
                raise BackendUnavailableError(
                    f"vision planner failed {self._failures} consecutive decisions: {exc}"
                ) from exc
            print(
                f"[vlm] decision failed ({self._failures}): {exc};"
                f" retrying in {self._failure_backoff_s:.0f}s",
                flush=True,
            )
            self._next_decision_at = time.monotonic() + self._failure_backoff_s
            return self._hold_chunk(context, duration=self._failure_backoff_s)
        self._failures = 0
        self._next_decision_at = time.monotonic() + self._decision_interval_s
        if action == "wait":
            self._last_action = 'wait（画面无合适目标或加载中）'
            print(f"[vlm] wait; reply: {reply.strip()[:120]!r}", flush=True)
            return self._hold_chunk(context, duration=self._decision_interval_s)
        assert x is not None and y is not None
        tap_x = round(rect.left + rect.width * x)
        tap_y = round(rect.top + rect.height * y)
        self._last_point = (tap_x, tap_y)
        self._last_action = f"tap({x:.3f},{y:.3f})"
        print(f"[vlm] tap ({x:.3f}, {y:.3f}) -> screen ({tap_x}, {tap_y})", flush=True)
        return self._tap_chunk(context, tap_x, tap_y)

    def _tap_chunk(self, context: PolicyContext, tap_x: int, tap_y: int) -> FastPolicyOutput:
        # Model inference can take tens of seconds, so chunks are stamped at
        # decision time — the observation that drove them is still bound via
        # the observation id, but the actuation window must start now.
        now = self._clock.now()
        chunk = ActionChunk(
            chunk_id=f"vlm-tap-{uuid.uuid4().hex[:8]}",
            observation_id=context.observation_id,
            generated_at=now,
            effective_from=now,
            expires_at=UGATime(now.value_ns + 1_000_000_000),
            tick_rate_hz=1.0,
            move_x=(0.0,),
            move_y=(0.0,),
            look_x=(0.0,),
            look_y=(0.0,),
            buttons=(int(ActionButton.INTERACT),),
            confidence=1.0,
            policy_version=self._policy_version,
            pointer_x=float(tap_x),
            pointer_y=float(tap_y),
        )
        return FastPolicyOutput(chunk, False, None, 30.0, 30.0)

    def _hold_chunk(self, context: PolicyContext, duration: float) -> FastPolicyOutput:
        ticks = max(1, int(duration * 30.0))
        hold = self._last_point
        if hold is None:
            rect = self._client_rect()
            hold = (
                round(rect.left + rect.width / 2),
                round(rect.top + rect.height / 2),
            )
        now = self._clock.now()
        chunk = ActionChunk(
            chunk_id=f"vlm-hold-{uuid.uuid4().hex[:8]}",
            observation_id=context.observation_id,
            generated_at=now,
            effective_from=now,
            expires_at=UGATime(now.value_ns + int(duration * 1_000_000_000)),
            tick_rate_hz=30.0,
            move_x=(0.0,) * ticks,
            move_y=(0.0,) * ticks,
            look_x=(0.0,) * ticks,
            look_y=(0.0,) * ticks,
            buttons=(0,) * ticks,
            confidence=1.0,
            policy_version=self._policy_version,
            pointer_x=float(hold[0]),
            pointer_y=float(hold[1]),
        )
        return FastPolicyOutput(chunk, False, None, 30.0, 30.0)
