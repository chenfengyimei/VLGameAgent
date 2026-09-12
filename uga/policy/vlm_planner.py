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
import contextlib
import importlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from threading import Event, Lock
from typing import Any

from uga.capture.frame import BufferHandle, BufferKind, Frame, PixelFormat
from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.policy.action_chunk import ActionButton, ActionChunk
from uga.policy.decision_journal import DecisionJournal, DecisionRecord, NullJournal
from uga.policy.fast_policy import FastPolicyOutput, PolicyContext
from uga.time.clock import ClockBackend, PerfCounterClock, UGATime
from uga.windows.coordinates import Rect

MAX_CONSECUTIVE_FAILURES = 5
MAX_ACTION_SEQUENCE = 4
MAX_STATIC_HOLDS = 10
MAX_WAIT_INTERVAL_S = 15.0
MAX_VISION_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_VISION_ERROR_DETAIL_BYTES = 4096
DEFAULT_FRAME_HISTORY_BYTES = 128 * 1024 * 1024
# A wait streak on a screen that never advances by itself (a reward popup
# that only closes on a blank-area tap) is a soft deadlock: escalation only
# slows the burn. Warn the model first; if it still refuses to act, press
# the bound back button once mechanically.
WAIT_WARN_STREAK = 6
WAIT_FORCE_BACK_STREAK = 10
_SUPPORTED_FORMATS = (PixelFormat.BGRA8, PixelFormat.RGBA8)
# Live GLM failure shape: {"action":"tap","x":0.622,0.415,...} — both
# fractions packed into the x slot with the "y" key dropped. The decode
# path repairs this only when the candidate has no "y" key at all.
_MERGED_XY_RE = re.compile(r'("x"\s*:\s*)(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)')


class PlannerReplyError(ContractViolation):
    """The vision model reply could not be parsed into a valid action."""


class VisionRateLimitedError(Exception):
    """The provider throttled the request; wait longer, do not count a failure."""


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


_GRID_COLOR_BGRA = b"\xff\x00\xff\xff"  # magenta, fully opaque


def _paint_grid(payload: bytearray, width: int, height: int, stride: int) -> None:
    """Draw 2px magenta lines every 10% of width/height in place.

    The grid anchors the model's coordinate estimates: vision models judge
    vertical positions poorly (live probe showed taps landing 1.5-2% below
    button text centers), and gridlines give the model a visual ruler for
    the fraction coordinates it must output.
    """
    row_pattern = _GRID_COLOR_BGRA * width
    for k in range(1, 10):
        y = round(height * k / 10)
        for dy in (0, 1):
            row = y + dy
            if 0 <= row < height:
                base = row * stride
                payload[base : base + width * 4] = row_pattern
    for k in range(1, 10):
        x = round(width * k / 10)
        for dx in (0, 1):
            col = x + dx
            if 0 <= col < width:
                offset = col * 4
                for row in range(height):
                    pixel = row * stride + offset
                    payload[pixel : pixel + 4] = _GRID_COLOR_BGRA


def encode_frame_png_with_grid(frame: Frame, *, max_width: int = 960) -> bytes:
    """Encode a frame with the 10% coordinate grid overlaid (see _paint_grid)."""
    handle = frame.buffer_handle
    if handle.kind != BufferKind.CPU_BYTES:
        raise ContractViolation("vision planner requires a CPU byte frame")
    payload = bytearray(handle.readonly_view())
    _paint_grid(payload, frame.width, frame.height, frame.stride_bytes)
    gridded = replace(
        frame,
        buffer_handle=BufferHandle(
            handle_id=f"{handle.handle_id}:grid",
            kind=handle.kind,
            size_bytes=len(payload),
            payload=bytes(payload),
        ),
    )
    return encode_frame_png(gridded, max_width=max_width)


class OpenAICompatibleVisionClient:
    """Minimal OpenAI-compatible chat client for local or cloud vision APIs."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_s: float = 30.0,
        disable_thinking: bool = False,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        if not base_url.strip() or not model.strip():
            raise ContractViolation("vision client requires a base URL and a model name")
        if not isinstance(timeout_s, (int, float)) or not 0 < timeout_s < float("inf"):
            raise ContractViolation("vision client timeout must be positive")
        root = base_url.rstrip("/")
        if not root.endswith("/chat/completions"):
            root = root + "/chat/completions"
        self._endpoint = root
        self._model = model
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._disable_thinking = disable_thinking
        self._extra_body = dict(extra_body) if extra_body else None

    def decide(self, *, images: list[bytes], instruction: str) -> str:
        """Send one or more frames (oldest first) plus the instruction."""
        if not images:
            raise ContractViolation("vision client requires at least one image")
        content: list[dict[str, Any]] = [
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,"
                    + base64.b64encode(image).decode("ascii")
                },
            }
            for image in images
        ]
        content.append({"type": "text", "text": instruction})
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
            "temperature": 0.0,
            # Thinking-style models (e.g. GLM-4.xV) spend tokens on reasoning
            # before the answer; a small budget yields an empty content field.
            "max_tokens": 4096,
        }
        if self._disable_thinking:
            # Zhipu-style switch: answer directly without a reasoning pass.
            payload["thinking"] = {"type": "disabled"}
        if self._extra_body:
            # Provider-specific request fields, e.g. DashScope Qwen3
            # {"enable_thinking": false} or sampling overrides.
            payload.update(self._extra_body)
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
                raw = response.read(MAX_VISION_RESPONSE_BYTES + 1)
                if len(raw) > MAX_VISION_RESPONSE_BYTES:
                    raise BackendUnavailableError("vision endpoint response is too large")
                body = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                # Rate limiting is not an outage: the caller must back off and
                # keep running instead of counting the provider as dead.
                raise VisionRateLimitedError("vision endpoint rate limited") from exc
            # The error body carries the provider's reason (e.g. DashScope
            # Arrearage when the account runs out of credit) — surface it or
            # the run log hides the actual cause behind a bare status code.
            detail = ""
            with contextlib.suppress(OSError, ValueError):
                detail = exc.read(MAX_VISION_ERROR_DETAIL_BYTES).decode(
                    "utf-8", "replace"
                )[:200]
            raise BackendUnavailableError(
                f"vision endpoint returned HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise BackendUnavailableError(f"vision endpoint unreachable: {exc}") from exc
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendUnavailableError("vision reply is missing message content") from exc
        if not isinstance(message, dict):
            raise BackendUnavailableError("vision reply message is malformed")
        reply_text = message.get("content")
        if not isinstance(reply_text, str) or not reply_text.strip():
            # Some providers park everything in reasoning_content when the
            # answer never fits; use it as a last resort so the loop can retry.
            reply_text = message.get("reasoning_content")
        if not isinstance(reply_text, str) or not reply_text.strip():
            raise BackendUnavailableError("vision reply content is empty")
        return reply_text


class FrameHistorySampler:
    """Continuously captures the target window (~1 Hz) on its own thread.

    Model inference blocks the observe loop for tens of seconds; this sampler
    keeps collecting frames during that gap so the next decision can see what
    happened while the model was thinking.
    """

    def __init__(
        self,
        *,
        capture: Callable[[], Frame],
        interval_s: float = 1.0,
        capacity: int = 60,
        max_bytes: int = DEFAULT_FRAME_HISTORY_BYTES,
    ) -> None:
        if not 0.0 < interval_s < float("inf"):
            raise ContractViolation("frame sampler interval must be positive")
        if capacity < 2:
            raise ContractViolation("frame sampler capacity must hold several frames")
        if max_bytes < 1:
            raise ContractViolation("frame sampler byte budget must be positive")
        self._capture = capture
        self._interval_s = interval_s
        self._capacity = capacity
        self._max_bytes = max_bytes
        self._history_bytes = 0
        self._history: deque[tuple[float, Frame]] = deque()
        self._lock = Lock()
        self._stop_event = Event()
        self._thread = threading.Thread(target=self._run, name="uga-frame-sampler", daemon=True)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_s):
            try:
                frame = self._capture()
            except Exception:  # noqa: BLE001 - sampler must never kill the run
                continue
            self._append(time.monotonic(), frame)

    def _append(self, captured_at: float, frame: Frame) -> None:
        size = frame.buffer_handle.size_bytes
        if size > self._max_bytes:
            return
        with self._lock:
            self._history.append((captured_at, frame))
            self._history_bytes += size
            while (
                len(self._history) > self._capacity
                or self._history_bytes > self._max_bytes
            ):
                _, evicted = self._history.popleft()
                self._history_bytes -= evicted.buffer_handle.size_bytes

    def latest(self) -> Frame | None:
        with self._lock:
            return self._history[-1][1] if self._history else None

    def select_bundle(
        self, last_inference_at: float | None, max_frames: int = 5
    ) -> list[Frame]:
        """Pick up to ``max_frames`` frames covering the last inference gap.

        Bundle order is oldest → newest:
        1. the first frame captured AFTER the previous inference (continuity
           anchor from the gap start),
        2. up to three evenly spaced frames from the remainder,
        3. the newest frame (the current decision target).
        """
        if max_frames < 1:
            raise ContractViolation("frame bundle size must be positive")
        with self._lock:
            history = list(self._history)
        if not history:
            return []
        pool = [
            item
            for item in history
            if last_inference_at is None or item[0] > last_inference_at
        ]
        if not pool:
            # Nothing new since the last inference (long static screen):
            # fall back to the newest frames we still hold.
            pool = history[-3:]
        if len(pool) <= max_frames:
            return [frame for _, frame in pool]
        first, *middle, last = pool
        picks = [first]
        take = max_frames - 2
        step = (len(middle) - 1) / take if take and len(middle) > 1 else 0
        seen: set[int] = set()
        for i in range(take):
            index = round(i * step) if step else min(i, len(middle) - 1)
            index = min(max(index, 0), len(middle) - 1)
            if index not in seen:
                seen.add(index)
                picks.append(middle[index])
        picks.append(last)
        return [frame for _, frame in picks[:max_frames]]


def parse_planner_reply(
    reply: str,
) -> tuple[str, float | None, float | None, str | None, str | None]:
    """Parse a single-action reply (compatibility wrapper over the sequence)."""
    return parse_planner_sequence(reply)[0]


def parse_planner_sequence(
    reply: str,
) -> list[tuple[str, float | None, float | None, str | None, str | None]]:
    """Parse a model reply into one or more actions (oldest first).

    Accepts a single action object or an ``actions`` array (up to
    ``MAX_ACTION_SEQUENCE`` steps — Lumine-style action chunking at the API
    level). Thinking-style models may emit description text that itself
    contains braces plus one or more JSON objects. Scan every balanced
    ``{...}`` segment and accept the NEWEST one that validates; anything
    else is a PlannerReplyError so the caller can retry instead of
    inventing actions.
    """
    text = reply.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    candidates: list[str] = []
    depth = 0
    start = -1
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                candidates.append(text[start : index + 1])
    if not candidates:
        raise PlannerReplyError("vision reply contains no JSON object")
    failure: PlannerReplyError | None = None
    for candidate in reversed(candidates):
        try:
            return _decode_sequence(candidate)
        except (json.JSONDecodeError, PlannerReplyError) as exc:
            failure = (
                exc if isinstance(exc, PlannerReplyError) else PlannerReplyError(str(exc))
            )
    if failure is None:
        failure = PlannerReplyError("vision reply contains no JSON object")
    raise failure


def _repair_merged_xy(candidate: str) -> str | None:
    """Rewrite the merged tap fractions when the ``y`` key is missing.

    Only fires on a candidate that has no ``y`` key, so valid replies are
    never rewritten; the caller only invokes it after a hard JSON failure.
    """
    if '"y"' in candidate:
        return None
    match = _MERGED_XY_RE.search(candidate)
    if match is None:
        return None
    return (
        candidate[: match.start()]
        + f'{match.group(1)}{match.group(2)},"y":{match.group(3)}'
        + candidate[match.end():]
    )


def _decode_sequence(
    candidate: str,
) -> list[tuple[str, float | None, float | None, str | None, str | None]]:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        repaired = _repair_merged_xy(candidate)
        if repaired is None:
            raise
        payload = json.loads(repaired)
    if not isinstance(payload, dict):
        raise PlannerReplyError("vision reply JSON is not an object")
    if "actions" in payload:
        steps = payload["actions"]
        if not isinstance(steps, list) or not steps:
            raise PlannerReplyError("vision reply actions must be a non-empty array")
        if len(steps) > MAX_ACTION_SEQUENCE:
            # Bounded chunk: keep the first steps, drop the overflow.
            steps = steps[:MAX_ACTION_SEQUENCE]
        sequence: list[tuple[str, float | None, float | None, str | None, str | None]] = []
        for step in steps:
            if not isinstance(step, dict):
                raise PlannerReplyError("vision reply action steps must be objects")
            sequence.append(_decode_payload(step))
        return sequence
    return [_decode_payload(payload)]


def _decode_payload(
    payload: dict[str, Any],
) -> tuple[str, float | None, float | None, str | None, str | None]:
    action = payload.get("action")
    step_raw = payload.get("step")
    step = str(step_raw) if isinstance(step_raw, str) and step_raw.strip() else None
    quest = payload.get("quest")
    reported = str(quest) if isinstance(quest, str) and quest.strip() else None
    if action == "press":
        button = payload.get("button")
        if not isinstance(button, str) or not button.strip():
            raise PlannerReplyError("vision press reply lacks a button name")
        button = button.strip().lower()
        if button not in ("jump", "menu", "confirm", "back", "primary", "secondary"):
            raise PlannerReplyError(f"vision press reply has unknown button: {button!r}")
        return "press", None, None, reported, button
    if action == "drag":
        # Endpoint B rides in the fifth slot as "x2,y2"; the dispatch layer
        # rebuilds the drag path from it.
        try:
            x1 = float(payload["x1"])
            y1 = float(payload["y1"])
            x2 = float(payload["x2"])
            y2 = float(payload["y2"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PlannerReplyError(
                "vision drag reply lacks numeric x1/y1/x2/y2 coordinates"
            ) from exc
        if not all(0.0 <= v <= 1.0 for v in (x1, y1, x2, y2)):
            raise PlannerReplyError("vision drag coordinates must be fractions within [0, 1]")
        return "drag", x1, y1, reported, f"{x2:.4f},{y2:.4f}"
    if action == "wait":
        return "wait", None, None, reported, step
    if action != "tap":
        raise PlannerReplyError(f"vision reply has unknown action: {action!r}")
    try:
        x = float(payload["x"])
        y = float(payload["y"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PlannerReplyError("vision tap reply lacks numeric x/y coordinates") from exc
    if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
        raise PlannerReplyError("vision tap coordinates must be fractions within [0, 1]")
    return "tap", x, y, reported, step


def _shared_instruction_lines(
    goal: str,
    last_action: str | None,
    screen_changed: bool | None,
    stuck_count: int,
    wait_streak: int,
) -> tuple[str, str, str, str]:
    history = (
        ""
        if last_action is None
        else f"上一次动作是 {last_action}。如果画面因此没有变化，请仔细重新观察，尝试别的目标。\n"
    )
    changed_line = (
        "上一次点击后画面没有变化——你点的位置无效，必须换不同的目标。\n"
        if screen_changed is False
        else ""
    )
    stuck_line = (
        f"警告：你已连续 {stuck_count} 次点击同一位置且画面毫无反应——请重新定位目标，"
        "或选择画面中完全不同的安全操作。\n"
        if stuck_count >= 3
        else ""
    )
    wait_line = (
        f"警告：你已连续 {wait_streak} 次选择等待且画面毫无推进——禁止再输出 wait。"
        "必须执行画面中可确认的具体动作，或在存在已确认绑定时输出 "
        '{"action":"press","button":"back"}。\n'
        if wait_streak >= WAIT_WARN_STREAK
        else ""
    )
    return history, changed_line, stuck_line, wait_line


def build_instruction(
    goal: str,
    last_action: str | None,
    quest: str | None,
    screen_changed: bool | None,
    stuck_count: int = 0,
    quest_step: str | None = None,
    quest_repeats: int = 0,
    wait_streak: int = 0,
) -> str:
    """Compose the environment-neutral decision prompt.

    Quest parameters remain in the shared callback signature so a policy can
    switch prompt strategies without changing its state machine. The generic
    strategy intentionally ignores them: it must not assume a quest tracker,
    a mobile UI, or a particular game economy.
    """
    del quest, quest_step, quest_repeats
    history, changed_line, stuck_line, wait_line = _shared_instruction_lines(
        goal, last_action, screen_changed, stuck_count, wait_streak
    )
    return (
        "你是一个通用游戏视觉操作智能体。请仔细观察这组按时间先后排序的截图"
        "（最后一张是当前画面，其余是历史画面，用于判断变化趋势）。\n"
        "截图叠加了间隔为画面宽高 10% 的洋红色坐标网格。"
        "输出坐标前先用网格校准，并只操作画面中确实可见的目标。\n"
        f"当前任务目标：{goal}。\n"
        f"{history}{changed_line}{stuck_line}{wait_line}"
        "坐标使用客户区比例：x、y 都必须在 0.0 到 1.0 之间。\n"
        "回复末尾必须包含一个 JSON 对象。单动作可使用：\n"
        '{"action":"tap","x":0.5,"y":0.5}\n'
        '{"action":"press","button":"confirm"}\n'
        '{"action":"drag","x1":0.2,"y1":0.8,"x2":0.2,"y2":0.3}\n'
        '{"action":"wait"}\n'
        f"连续操作可用 actions 数组，但最多 {MAX_ACTION_SEQUENCE} 步。"
        "不要编造不存在的元素，不确定或正在加载时选择 wait。\n"
    )


def build_android_quest_instruction(
    goal: str,
    last_action: str | None,
    quest: str | None,
    screen_changed: bool | None,
    stuck_count: int = 0,
    quest_step: str | None = None,
    quest_repeats: int = 0,
    wait_streak: int = 0,
) -> str:
    """Compose the Android quest-tracker prompt for an opted-in profile."""
    history, changed_line, shared_stuck_line, wait_line = _shared_instruction_lines(
        goal, last_action, screen_changed, stuck_count, wait_streak
    )
    if quest_repeats >= 6:
        # Anti-echo fallback: the model kept echoing the memorized quest back,
        # so hide the memory text entirely for one round — the quest field can
        # only be filled by actually reading the tracker on screen.
        quest_line = (
            "记忆任务已多轮未变化，本轮隐藏。quest 字段必须逐字写你此刻从画面左上角"
            "任务追踪面板最上方读到的任务原文。\n"
        )
    elif quest:
        quest_line = (
            f"记忆任务（上次画面读取，可能已过期）：{quest}\n"
            "quest 字段必须逐字写你此刻从画面左上角任务追踪面板最上方读到的任务原文——"
            "禁止照抄上面的记忆任务；若画面显示的任务与记忆不同，立即写画面上的新值。\n"
        )
    else:
        quest_line = "quest 字段必须逐字写你此刻从画面左上角任务追踪面板最上方读到的任务原文。\n"
    step_line = (
        f"当前任务步骤：{quest_step}（完成后更新 step 字段为下一步）。\n"
        if quest_step
        else ""
    )
    stuck_line = (
        f"警告：你已连续 {stuck_count} 次点击同一位置且画面毫无反应——你的坐标定位很可能错了"
        "（系统已自动在附近偏移点击尝试）。请重新仔细观察按钮的实际位置（对照它旁边的文字、"
        "图标重新定位），或改点完全不同的目标（如左上角返回按钮、任务面板）。\n"
        if stuck_count >= 3
        else shared_stuck_line
    )
    return (
        "你是一个安卓游戏自动操作智能体。请仔细观察这组按时间先后排序的游戏截图"
        "（最后一张是当前画面，其余是之前几秒的历史画面，用于判断画面变化趋势）。\n"
        "截图上叠加了洋红色网格线（横向与纵向各 9 条，相邻间隔 10% 画面宽高）："
        "顶部第一条横线在 10% 高度，依次向下为 20%、30%…；左侧第一条竖线在 10% 宽度。"
        "输出坐标前先用网格线校准：例如目标位于 40% 横线下方一点，y 就应略大于 0.40。\n"
        "点击按钮时瞄准按钮文字的正中心（不是按钮框的下边缘）。\n"
        f"当前任务目标：{goal}。\n"
        f"{quest_line}"
        f"{step_line}"
        f"{history}"
        f"{changed_line}"
        f"{stuck_line}"
        f"{wait_line}"
        "坐标以百分比表示：x 为横向 0.0（最左）~1.0（最右），y 为纵向 0.0（最上）~1.0（最下）。\n"
        "回答格式（严格遵守两行）：\n"
        "第一行：用一句话（不超过50字）描述画面状态，读出你看到的按钮上的文字。\n"
        "第二行：只输出一个 JSON 对象。单个动作的格式：\n"
        '{"action":"tap","x":<按钮中心的横向百分比>,"y":<按钮中心的纵向百分比>}\n'
        '当需要连续多个操作时（如推进多段对话、关闭多个弹窗），用 actions 数组输出最多 '
        f'{MAX_ACTION_SEQUENCE} 步，每步之间画面会自动等待变化：\n'
        '{"actions":[{"action":"tap","x":0.5,"y":0.6},{"action":"tap","x":0.5,"y":0.7}]}\n'
        '必填字段 quest：把你此刻从画面左上角任务追踪面板最上方读到的任务原文逐字写进 quest 字段'
        '（每次都要重新读画面，即使没变也照写；绝不照抄提示中的记忆任务）；'
        '若追踪面板被界面完全遮挡看不到，quest 字段写 NOT_VISIBLE；'
        '顶部滚动的全服公告/广播（如“恭喜某某获得某物”）不是任务，绝不能写进 quest 字段：\n'
        '可选字段 step：任务进行到哪一步时写当前步骤（如"打开灵宠界面"）：\n'
        '需要按实体按键时（用于跳过剧情、确认、返回等）：{"action":"press","button":"confirm"}，'
        "可用 button 值：jump、menu、confirm、back（需游戏支持）\n"
        '需要拖拽时（虚拟摇杆移动、滑动界面）：{"action":"drag","x1":0.14,"y1":0.78,"x2":0.14,"y2":0.30}'
        "（从摇杆中心向上拖拽即向前移动；拖拽时长约 1 秒）\n"
        '{"action":"tap","x":0.2,"y":0.3,"quest":"与桃夭对话"}\n'
        '或画面在加载、无合适目标时输出：{"action":"wait"}\n'
        "识别要点：点购买/使用类按钮前先看资源数量（如金币、令）——数量不足或为 0 时该按钮无效，"
        "不要点它，优先改用列表/背包中已有的资源完成任务；灰色或暗淡的按钮表示当前不可用，不要点击。\n"
        "规则：坐标必须在 0~1 之间；优先点击文字与任务目标相关的按钮；"
        "不要编造画面中不存在的元素；"
        "第二行的 JSON 对象在任何情况下都必须出现在回复末尾，绝对不能省略。\n"
    )


class VlmPlannerPolicy:
    """Perception-driven tap policy backed by a vision-language model.

    v2 additions over the plain single-frame policy:
    - a frame-history sampler feeds the model up to ``max_bundle_frames``
      chronologically ordered screenshots covering the last inference gap,
    - the loop remembers the main-quest text (reported by the model via the
      optional ``quest`` reply field) and re-injects it into every prompt,
    - a frame-difference check flags ineffective repeated taps and injects an
      anti-stuck directive after three no-effect taps on the same point.
    """

    def __init__(
        self,
        *,
        client: OpenAICompatibleVisionClient,
        frame_source: Callable[[], Frame],
        client_rect: Callable[[], Rect],
        goal: str,
        decision_interval_s: float = 6.0,
        failure_backoff_s: float = 10.0,
        policy_version: str = "vlm-planner-v2",
        max_image_width: int = 720,
        clock: ClockBackend | None = None,
        screen_change_threshold: float = 0.015,
        sampler: FrameHistorySampler | None = None,
        journal: DecisionJournal | None = None,
        available_buttons: frozenset[str] = frozenset(
            {"jump", "menu", "confirm", "back", "primary", "secondary"}
        ),
        instruction_builder: Callable[
            [str, str | None, str | None, bool | None, int, str | None, int, int], str
        ] = build_instruction,
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
        self._screen_change_threshold = screen_change_threshold
        self._clock = clock if clock is not None else PerfCounterClock()
        self._next_decision_at = 0.0
        self._failures = 0
        self._rate_limit_backoff_s = 30.0
        self._last_point: tuple[int, int] | None = None
        self._last_action: str | None = None
        # --- v2 state: quest memory + anti-stuck --------------------------
        self._quest: str | None = None
        self._quest_step: str | None = None
        self._quest_repeats = 0
        self._last_action_changed: bool | None = None
        self._stuck_taps = 0
        self._last_tap_point: tuple[int, int] | None = None
        self._last_tap_ref: tuple[float, float, bytes] | None = None
        self._recent_taps: deque[tuple[int, int]] = deque(maxlen=3)
        self._perturb_rounds = 0
        self._wait_streak = 0
        self._last_decision_started_mono: float | None = None
        self._sampler = sampler
        self._last_decision_digest: bytes | None = None
        self._stale_discards = 0
        self._journal = journal if journal is not None else NullJournal()
        self._available_buttons = available_buttons
        self._instruction_builder = instruction_builder
        self._pending_actions: deque[
            tuple[str, float | None, float | None, str | None, str | None]
        ] = deque()
        self._static_holds = 0

    @property
    def policy_version(self) -> str:
        return self._policy_version

    def _frame_digest(self, frame: Frame) -> bytes:
        """Sampled bytes of the frame payload (every 16th byte) for diffing."""
        view = frame.buffer_handle.readonly_view()
        return bytes(view)[::16]

    def _frame_changed_since_decision(self, frame: Frame) -> bool:
        """True when this frame differs noticeably from the last decision's.

        Compares sampled payload bytes; a different length (window resize)
        always counts as a change. The reference is the frame at the last
        successful decision, so the flag answers "did my last action have
        any visible effect".
        """
        digest = self._frame_digest(frame)
        previous = self._last_decision_digest
        if previous is None:
            return True
        if len(digest) != len(previous):
            return True
        differing = sum(1 for a, b in zip(digest, previous, strict=True) if a != b)
        return differing / len(digest) > self._screen_change_threshold

    def _region_digest(self, frame: Frame, fx: float, fy: float) -> bytes:
        """Sampled bytes of a ±8% box around the tap target (client fractions)."""
        view = frame.buffer_handle.readonly_view()
        payload = bytes(view)
        width, height, stride = frame.width, frame.height, frame.stride_bytes
        x0 = max(0, int((fx - 0.08) * width)) * 4
        x1 = min(width, int((fx + 0.08) * width)) * 4
        y0 = max(0, int((fy - 0.08) * height))
        y1 = min(height, int((fy + 0.08) * height))
        rows = [payload[y * stride + x0 : y * stride + x1] for y in range(y0, y1)]
        return b"".join(rows)[::16]

    def _tap_target_stale(
        self, decided: Frame, fresh: Frame, fx: float, fy: float
    ) -> bool:
        """True when the tap target area changed between decision and now.

        Only the target box is compared — full-screen animations (water,
        characters, effects) elsewhere must not invalidate the tap. A
        different region size (window resize) counts as stale.
        """
        if (
            decided.window_identity != fresh.window_identity
            or decided.client_rect != fresh.client_rect
            or decided.width != fresh.width
            or decided.height != fresh.height
        ):
            return True
        decided_region = self._region_digest(decided, fx, fy)
        fresh_region = self._region_digest(fresh, fx, fy)
        if not decided_region or len(decided_region) != len(fresh_region):
            return True
        differing = sum(
            1 for a, b in zip(decided_region, fresh_region, strict=True) if a != b
        )
        return differing / len(decided_region) > 0.25

    def _bundle_frames(self, current: Frame, boundary: float | None) -> list[Frame]:
        if self._sampler is None:
            return [current]
        # Four history frames + the freshest main-loop frame = max 5 images.
        bundle = self._sampler.select_bundle(boundary, max_frames=4)
        return [*bundle, current]

    def _region_changed_since(
        self, reference: bytes, frame: Frame, fx: float, fy: float
    ) -> bool:
        """True when the ±8% box at the last tap point visibly changed."""
        region = self._region_digest(frame, fx, fy)
        if not reference or len(reference) != len(region):
            return True
        differing = sum(
            1 for a, b in zip(reference, region, strict=True) if a != b
        )
        return differing / len(reference) > 0.25

    def infer(self, context: PolicyContext) -> FastPolicyOutput:
        now = time.monotonic()
        if now < self._next_decision_at:
            wait_s = max(self._next_decision_at - now, 0.05)
            return self._hold_chunk(context, duration=wait_s)
        # --- hybrid thinking (Lumine-style): skip inference when the screen
        # is unchanged AND nothing is queued. A static screen means the last
        # decision still applies; re-asking the model would waste tokens.
        current_frame = self._frame_source()
        digest = self._frame_digest(current_frame)
        # Effect check for the previous tap: it worked when the whole screen
        # changed OR at least the tapped region did (button highlight,
        # dialog, page flip). Animating backgrounds flip the full-frame
        # digest constantly, so without the region channel every miss on a
        # busy screen looked "effective" — and misses were invisible.
        screen_changed = self._frame_changed_since_decision(current_frame)
        region_effect = False
        if self._last_tap_ref is not None:
            ref_x, ref_y, ref_region = self._last_tap_ref
            region_effect = self._region_changed_since(ref_region, current_frame, ref_x, ref_y)
        self._last_action_changed = screen_changed or region_effect
        self._last_tap_ref = None
        if (
            not self._pending_actions
            and self._last_decision_digest is not None
            and digest == self._last_decision_digest
            # Static holds extend a WAITING state only. After an ineffective
            # tap the loop must re-decide (or probe nearby), never nap — a
            # missed click on a calm screen would otherwise stall silently.
            and self._last_action is not None
            and self._last_action.startswith("wait")
            and self._static_holds < MAX_STATIC_HOLDS
        ):
            self._static_holds += 1
            self._next_decision_at = now + self._decision_interval_s
            self._journal.record(
                DecisionRecord(
                    timestamp=time.time(),
                    kind="static_hold",
                    latency_s=None,
                    action=self._last_action,
                    detail=f"screen unchanged; hold {self._static_holds}/{MAX_STATIC_HOLDS}",
                    quest=self._quest,
                    quest_step=self._quest_step,
                    images=None,
                    reply_head=None,
                )
            )
            return self._hold_chunk(context, duration=self._decision_interval_s)
        if digest != self._last_decision_digest:
            self._static_holds = 0
        # --- queued multi-action sequence (action chunking): replay the
        # remaining steps without paying for another inference round-trip.
        if self._pending_actions:
            queued = self._pending_actions.popleft()
            return self._execute_action(context, current_frame, queued, queued_reply=True)
        # The bundle must cover the WHOLE gap since the previous decision
        # STARTED — inference itself takes tens of seconds and those frames
        # (what happened while the model was thinking) are exactly the
        # continuity the model needs; stamping at decision START achieves it.
        boundary = self._last_decision_started_mono
        self._last_decision_started_mono = now
        reply: str = ""
        images: list[bytes] = []
        inference_started = time.monotonic()
        try:
            rect = self._client_rect()
            images = [
                encode_frame_png_with_grid(frame, max_width=self._max_image_width)
                for frame in self._bundle_frames(current_frame, boundary)
            ]
            instruction = self._instruction_builder(
                self._goal,
                self._last_action,
                self._quest,
                self._last_action_changed,
                self._stuck_taps,
                self._quest_step,
                self._quest_repeats,
                self._wait_streak,
            )
            reply = self._client.decide(images=images, instruction=instruction)
            sequence = parse_planner_sequence(reply)
        except VisionRateLimitedError:
            # Throttling is transient by definition: grow the wait instead of
            # counting the provider as dead, and never let it kill the run.
            self._rate_limit_backoff_s = min(self._rate_limit_backoff_s * 2.0, 600.0)
            wait_s = max(self._rate_limit_backoff_s, 60.0)
            print(
                f"[vlm] rate limited; pausing decisions for {wait_s:.0f}s",
                flush=True,
            )
            self._journal.record(
                DecisionRecord(
                    timestamp=time.time(),
                    kind="rate_limited",
                    latency_s=time.monotonic() - inference_started,
                    action=None,
                    detail=f"backoff {wait_s:.0f}s",
                    quest=self._quest,
                    quest_step=self._quest_step,
                    images=len(images),
                    reply_head=None,
                )
            )
            self._next_decision_at = time.monotonic() + wait_s
            return self._hold_chunk(context, duration=wait_s)
        except Exception as exc:
            reply_head = f"; reply head: {reply.strip()[:120]!r}" if reply else ""
            self._failures += 1
            self._journal.record(
                DecisionRecord(
                    timestamp=time.time(),
                    kind="failure",
                    latency_s=time.monotonic() - inference_started,
                    action=None,
                    detail=f"{type(exc).__name__}: {exc}"[:160],
                    quest=self._quest,
                    quest_step=self._quest_step,
                    images=len(images),
                    reply_head=reply.strip()[:120] or None,
                )
            )
            if self._failures >= MAX_CONSECUTIVE_FAILURES:
                raise BackendUnavailableError(
                    f"vision planner failed {self._failures} consecutive decisions: {exc}"
                ) from exc
            print(
                f"[vlm] decision failed ({self._failures}): {exc}{reply_head};"
                f" retrying in {self._failure_backoff_s:.0f}s",
                flush=True,
            )
            self._next_decision_at = time.monotonic() + self._failure_backoff_s
            return self._hold_chunk(context, duration=self._failure_backoff_s)
        self._failures = 0
        self._rate_limit_backoff_s = 30.0
        self._next_decision_at = time.monotonic() + self._decision_interval_s
        self._last_decision_digest = self._frame_digest(current_frame)
        head, *tail = sequence
        for step in reversed(tail):
            self._pending_actions.append(step)
        self._journal.record(
            DecisionRecord(
                timestamp=time.time(),
                kind="decision",
                latency_s=time.monotonic() - inference_started,
                action=None,
                detail=f"{len(sequence)} step(s); reply: {reply.strip()[:140]}",
                quest=self._quest,
                quest_step=self._quest_step,
                images=len(images),
                reply_head=reply.strip()[:160],
            )
        )
        return self._execute_action(context, current_frame, head, rect=rect)

    def _execute_action(
        self,
        context: PolicyContext,
        decision_frame: Frame,
        step: tuple[str, float | None, float | None, str | None, str | None],
        *,
        queued_reply: bool = False,
        rect: Rect | None = None,
    ) -> FastPolicyOutput:
        if rect is None:
            rect = self._client_rect()
        action, x, y, quest, reported_step = step
        if quest:
            if quest.replace(" ", "").replace("_", "").upper().startswith("NOTVISIBLE"):
                # The tracker is hidden behind a full-screen UI. The sentinel
                # must never erase the last real quest — that memory is the
                # context that steers the model back once the UI closes.
                pass
            elif quest != self._quest:
                print(f"[vlm] quest updated: {quest}", flush=True)
                self._quest = quest
                self._quest_repeats = 0
            else:
                # Same text again: count it so the anti-echo fallback can
                # eventually hide the memory and force a fresh screen read.
                self._quest_repeats += 1
        if reported_step and reported_step != self._quest_step:
            print(f"[vlm] quest step: {reported_step}", flush=True)
            self._quest_step = reported_step
        action_label = (
            "wait" if action == "wait" else
            f"press({reported_step})" if action == "press" else
            f"drag({x:.2f},{y:.2f})" if action == "drag" and x is not None and y is not None else
            f"tap({x:.2f},{y:.2f})" if action == "tap" and x is not None and y is not None else
            action
        )
        self._journal.record(
            DecisionRecord(
                timestamp=time.time(),
                kind="queued" if queued_reply else "action",
                latency_s=None,
                action=action_label,
                detail=None,
                quest=self._quest,
                quest_step=self._quest_step,
                images=None,
                reply_head=None,
            )
        )
        if queued_reply:
            # Queued steps replay on later frames; the decision interval was
            # already advanced when the sequence was parsed.
            pass
        else:
            self._next_decision_at = time.monotonic() + self._decision_interval_s
        if action == "wait":
            if not queued_reply:
                # Consecutive waits escalate the next interval: on animated
                # screens the full-frame digest never matches, so the static
                # hold cannot help — waiting in a cinematic or event banner
                # should not burn a ~9s inference every 3 seconds. Any real
                # action resets the streak.
                self._wait_streak += 1
                if (
                    self._wait_streak >= WAIT_FORCE_BACK_STREAK
                    and "back" in self._available_buttons
                ):
                    # Mechanical escape: the model has waited many times on
                    # a screen that will not advance by itself (e.g. a
                    # reward popup that only closes on a blank-area tap).
                    # Prompt warnings cannot cure passive waiting — press the
                    # bound back button once and let the model re-observe.
                    print(
                        f"[vlm] wait x{self._wait_streak}; forcing back press "
                        "to dismiss a stuck overlay",
                        flush=True,
                    )
                    self._journal.record(
                        DecisionRecord(
                            timestamp=time.time(),
                            kind="action",
                            latency_s=None,
                            action="press(back) forced by wait streak",
                            detail=(
                                f"{self._wait_streak} consecutive waits on an "
                                "unchanged screen; system pressed back"
                            ),
                            quest=self._quest,
                            quest_step=self._quest_step,
                            images=None,
                            reply_head=None,
                        )
                    )
                    self._wait_streak = 0
                    self._last_action = 'press(back)（连续等待过久，系统强制返回）'
                    self._next_decision_at = (
                        time.monotonic() + self._decision_interval_s
                    )
                    return self._press_chunk(context, "back")
                escalated = min(
                    self._decision_interval_s * (2 ** self._wait_streak),
                    MAX_WAIT_INTERVAL_S,
                )
                self._next_decision_at = time.monotonic() + escalated
                self._last_action = 'wait（画面无合适目标或加载中）'
                print(
                    f"[vlm] wait x{self._wait_streak}; next decision in {escalated:.0f}s",
                    flush=True,
                )
                return self._hold_chunk(context, duration=escalated)
            self._last_action = 'wait（画面无合适目标或加载中）'
            print(f"[vlm] wait (queued={queued_reply})", flush=True)
            return self._hold_chunk(context, duration=self._decision_interval_s)
        self._wait_streak = 0
        if action == "press":
            # The fifth slot carries the button name for press actions.
            if not isinstance(reported_step, str) or not reported_step:
                raise PlannerReplyError("vision press reply lacks a button name")
            if reported_step not in self._available_buttons:
                # No confirmed binding in this profile: a press here would
                # produce an empty chunk and crash the pipeline — skip it
                # and re-decide on the unchanged screen immediately.
                print(
                    f"[vlm] press {reported_step} skipped (no confirmed binding)",
                    flush=True,
                )
                self._journal.record(
                    DecisionRecord(
                        timestamp=time.time(),
                        kind="action",
                        latency_s=None,
                        action=f"press({reported_step}) skipped",
                        detail="button has no confirmed binding in this profile",
                        quest=self._quest,
                        quest_step=None,
                        images=None,
                        reply_head=None,
                    )
                )
                self._next_decision_at = time.monotonic()
                return self._hold_chunk(context, duration=0.05)
            self._last_action = f"press({reported_step})"
            print(f"[vlm] press {reported_step} (queued={queued_reply})", flush=True)
            return self._press_chunk(context, reported_step)
        if action == "drag":
            # The fifth slot carries "x2,y2" for drag actions.
            if not isinstance(reported_step, str) or "," not in reported_step:
                raise PlannerReplyError("vision drag reply lacks end coordinates")
            assert x is not None and y is not None
            x2s, y2s = reported_step.split(",", 1)
            x2 = float(x2s)
            y2 = float(y2s)
            self._last_action = f"drag({x:.2f},{y:.2f}->{x2:.2f},{y2:.2f})"
            print(f"[vlm] drag ({x:.3f},{y:.3f}) -> ({x2:.3f},{y2:.3f})", flush=True)
            return self._drag_chunk(context, rect, x, y, x2, y2)
        # Freshness guard: the model replied to a screenshot that is now
        # seconds old (inference latency). If the area it decided to tap has
        # changed meanwhile, the tap would land on a stale layout — drop the
        # decision and immediately re-decide on the live frame. A stale target
        # is never allowed through merely because earlier replies were stale.
        if x is None or y is None:
            raise PlannerReplyError(f"vision {action} reply lacks tap coordinates")
        if self._tap_target_stale(decision_frame, self._frame_source(), x, y):
            self._stale_discards += 1
            self._pending_actions.clear()
            print(
                "[vlm] tap target stale (screen changed during inference);"
                " re-deciding on the fresh frame",
                flush=True,
            )
            self._journal.record(
                DecisionRecord(
                    timestamp=time.time(),
                    kind="stale_discard",
                    latency_s=None,
                    action=action_label,
                    detail=f"discards: {self._stale_discards}; queue cleared",
                    quest=self._quest,
                    quest_step=self._quest_step,
                    images=None,
                    reply_head=None,
                )
            )
            self._next_decision_at = time.monotonic()
            return self._hold_chunk(context, duration=0.05)
        self._stale_discards = 0
        tap_x = round(rect.left + rect.width * x)
        tap_y = round(rect.top + rect.height * y)
        self._last_point = (tap_x, tap_y)
        self._last_action = f"tap({x:.3f},{y:.3f})"
        # Cluster-based ineffective-tap detection: the model can be confident
        # about a button it keeps MISSING (wrong grounding); a prompt warning
        # alone does not fix its coordinates. Count taps that land within a
        # small radius of each other while the screen shows no effect.
        if not queued_reply:
            radius_x = rect.width * 0.03
            radius_y = rect.height * 0.03
            clustered = len(self._recent_taps) >= 2 and all(
                abs(px - tap_x) <= radius_x and abs(py - tap_y) <= radius_y
                for px, py in self._recent_taps
            )
            if clustered and self._last_action_changed is False:
                self._stuck_taps += 1
            else:
                self._stuck_taps = 0
                self._perturb_rounds = 0
            self._recent_taps.append((tap_x, tap_y))
            # Auto-probe (the human 'wiggle the mouse' fix): after repeated
            # ineffective taps on one spot, the system itself taps a ring of
            # nearby offsets — the model's intent stays, its aim gets a
            # mechanical second chance. Bounded to two probe rounds per
            # cluster so a permanently wrong target still escalates to the
            # 'switch target' prompt warning.
            if self._stuck_taps >= 3 and self._perturb_rounds < 2:
                self._perturb_rounds += 1
                offsets = (
                    (0.03, 0.0), (-0.03, 0.0), (0.0, 0.03), (0.0, -0.03),
                    (0.021, 0.021), (-0.021, 0.021), (0.021, -0.021), (-0.021, -0.021),
                )
                for dx, dy in offsets:
                    probe_x = min(max(x + dx, 0.0), 1.0)
                    probe_y = min(max(y + dy, 0.0), 1.0)
                    self._pending_actions.append(("tap", probe_x, probe_y, None, None))
                print(
                    f"[vlm] tap ineffective x{self._stuck_taps} at ({x:.3f},{y:.3f});"
                    " auto-probing 8 nearby offsets",
                    flush=True,
                )
            elif (
                self._stuck_taps >= 6
                and self._perturb_rounds >= 2
                and "back" in self._available_buttons
            ):
                # Both probe rounds are spent and the model is STILL tapping
                # the same dead spot — the human fix at this point is
                # pressing back to close the topmost UI and re-observe.
                # Prompt warnings cannot cure confident-but-wrong grounding;
                # the wait path has the same escape for passive waiting.
                print(
                    f"[vlm] tap ineffective x{self._stuck_taps} at "
                    f"({x:.3f},{y:.3f}); pressing back to dismiss the stuck UI",
                    flush=True,
                )
                self._journal.record(
                    DecisionRecord(
                        timestamp=time.time(),
                        kind="action",
                        latency_s=None,
                        action="press(back) forced by ineffective taps",
                        detail=(
                            f"{self._stuck_taps} clustered ineffective taps with"
                            " probe rounds exhausted; system pressed back"
                        ),
                        quest=self._quest,
                        quest_step=self._quest_step,
                        images=None,
                        reply_head=None,
                    )
                )
                self._pending_actions.clear()
                self._stuck_taps = 0
                self._perturb_rounds = 0
                self._last_action = "press(back)（连续无效点击，系统强制返回）"
                return self._press_chunk(context, "back")
        self._last_tap_point = (tap_x, tap_y)
        # Region reference for the next decision's effect check: was the
        # tapped box visibly different after the tap (highlight/dialog)?
        if not queued_reply:
            self._last_tap_ref = (
                x,
                y,
                self._region_digest(decision_frame, x, y),
            )
        print(f"[vlm] tap ({x:.3f}, {y:.3f}) -> screen ({tap_x}, {tap_y})", flush=True)
        return self._tap_chunk(context, tap_x, tap_y)

    def _press_chunk(self, context: PolicyContext, button: str) -> FastPolicyOutput:
        try:
            button_flag = ActionButton[button.upper()]
        except KeyError as exc:
            raise PlannerReplyError(
                f"vision reply press has unknown button: {button!r}"
            ) from exc
        # Model inference can take tens of seconds, so chunks are stamped at
        # decision time — the observation that drove them is still bound via
        # the observation id, but the actuation window must start now.
        now = self._clock.now()
        chunk = ActionChunk(
            chunk_id=f"vlm-press-{uuid.uuid4().hex[:8]}",
            observation_id=context.observation_id,
            generated_at=now,
            effective_from=now,
            expires_at=UGATime(now.value_ns + 1_000_000_000),
            tick_rate_hz=1.0,
            move_x=(0.0,),
            move_y=(0.0,),
            look_x=(0.0,),
            look_y=(0.0,),
            buttons=(int(button_flag),),
            confidence=1.0,
            policy_version=self._policy_version,
        )
        return FastPolicyOutput(chunk, False, None, 30.0, 30.0)

    def _drag_chunk(
        self,
        context: PolicyContext,
        rect: Rect,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> FastPolicyOutput:
        """Pressed-move-release along a straight client-fraction path.

        The path is interpolated in physical pixels; expand_action_chunk
        presses at the first tick, keeps the pointer moving through the
        middle ticks, and releases at the last — the emulator translates
        that into an Android touch-drag (verified live on MuMu).
        """
        start_x = round(rect.left + rect.width * x1)
        start_y = round(rect.top + rect.height * y1)
        end_x = round(rect.left + rect.width * x2)
        end_y = round(rect.top + rect.height * y2)
        steps = 24
        path = tuple(
            (
                start_x + (end_x - start_x) * i / (steps - 1),
                start_y + (end_y - start_y) * i / (steps - 1),
            )
            for i in range(steps)
        )
        now = self._clock.now()
        # ~1.2s total at 30 Hz gives the emulator time to register the drag.
        ticks = 36
        chunk = ActionChunk(
            chunk_id=f"vlm-drag-{uuid.uuid4().hex[:8]}",
            observation_id=context.observation_id,
            generated_at=now,
            effective_from=now,
            expires_at=UGATime(now.value_ns + int(ticks / 30.0 * 1_000_000_000)),
            tick_rate_hz=30.0,
            move_x=(0.0,) * ticks,
            move_y=(0.0,) * ticks,
            look_x=(0.0,) * ticks,
            look_y=(0.0,) * ticks,
            buttons=(0,) * ticks,
            confidence=1.0,
            policy_version=self._policy_version,
            pointer_drag=path,
        )
        return FastPolicyOutput(chunk, False, None, 30.0, 30.0)

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
        hold = self._last_point
        if hold is None:
            rect = self._client_rect()
            hold = (
                round(rect.left + rect.width / 2),
                round(rect.top + rect.height / 2),
            )
        now = self._clock.now()
        # A hold carries no buttons or axes.  Absolute pointer placement is
        # stateless, so schedule it once and keep the chunk/lease lifetime for
        # the requested wait.  Emitting it at 30 Hz created thousands of
        # duplicate mouse moves and poor execution ratios when observations
        # replaced the lease before the queue drained.
        chunk = ActionChunk(
            chunk_id=f"vlm-hold-{uuid.uuid4().hex[:8]}",
            observation_id=context.observation_id,
            generated_at=now,
            effective_from=now,
            expires_at=UGATime(now.value_ns + int(duration * 1_000_000_000)),
            tick_rate_hz=1.0,
            move_x=(0.0,),
            move_y=(0.0,),
            look_x=(0.0,),
            look_y=(0.0,),
            buttons=(0,),
            confidence=1.0,
            policy_version=self._policy_version,
            pointer_x=float(hold[0]),
            pointer_y=float(hold[1]),
        )
        return FastPolicyOutput(chunk, False, None, 30.0, 30.0)
