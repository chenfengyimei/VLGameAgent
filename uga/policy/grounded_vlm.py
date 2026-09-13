from __future__ import annotations

import json
import time
import uuid
from collections.abc import Sequence
from typing import Any, Protocol

from uga.capture.frame import BufferHandle, BufferKind, Frame
from uga.core.errors import BackendUnavailableError, ContractViolation
from uga.gui.schema import GuiActionKind
from uga.perception.builder import normalize_visible_text
from uga.perception.schema import (
    ActionRisk,
    DecisionKind,
    GoalStatus,
    GroundedAction,
    NormalizedBox,
    PerceptionSnapshot,
    PlannerOutcome,
    WaitReason,
)
from uga.policy.decision_journal import DecisionJournal, DecisionRecord, NullJournal
from uga.policy.vlm_planner import PlannerReplyError, encode_frame_png

GROUNDING_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "uga_grounded_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "kind",
                "scene_summary",
                "visible_text",
                "goal_status",
                "confidence",
                "action",
                "wait_reason",
                "explanation",
            ],
            "properties": {
                "kind": {"enum": [item.value for item in DecisionKind]},
                "scene_summary": {"type": "string", "maxLength": 160},
                "visible_text": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 80},
                    "maxItems": 16,
                },
                "goal_status": {"enum": [item.value for item in GoalStatus]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "wait_reason": {
                    "anyOf": [
                        {"enum": [item.value for item in WaitReason]},
                        {"type": "null"},
                    ]
                },
                "explanation": {"type": "string", "maxLength": 240},
                "action": {
                    "anyOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "kind",
                                "target_label",
                                "target_bbox",
                                "expected_effect",
                                "confidence",
                                "risk",
                                "key",
                            ],
                            "properties": {
                                "kind": {
                                    "enum": [
                                        GuiActionKind.CLICK.value,
                                        GuiActionKind.KEY.value,
                                        GuiActionKind.HOTKEY.value,
                                    ]
                                },
                                "target_label": {"type": "string", "maxLength": 80},
                                "target_bbox": {
                                    "anyOf": [
                                        {"type": "null"},
                                        {
                                            "type": "array",
                                            "items": {
                                                "type": "number",
                                                "minimum": 0,
                                                "maximum": 1,
                                            },
                                            "minItems": 4,
                                            "maxItems": 4,
                                        },
                                    ]
                                },
                                "expected_effect": {"type": "string", "maxLength": 160},
                                "confidence": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 1,
                                },
                                "risk": {"enum": [item.value for item in ActionRisk]},
                                "key": {
                                    "anyOf": [
                                        {"type": "string", "maxLength": 32},
                                        {"type": "null"},
                                    ]
                                },
                            },
                        },
                    ]
                },
            },
        },
    },
}


class StructuredVisionClient(Protocol):
    def decide(
        self,
        *,
        images: list[bytes],
        instruction: str,
        response_format: dict[str, Any] | None = None,
    ) -> str: ...


def crop_frame(frame: Frame, box: NormalizedBox, *, padding: float = 0.02) -> Frame:
    if not 0.0 <= padding <= 0.25:
        raise ContractViolation("frame crop padding must be in [0, 0.25]")
    if frame.buffer_handle.kind != BufferKind.CPU_BYTES:
        raise ContractViolation("frame crop requires a CPU-addressable frame")
    left = max(0, int((box.left - padding) * frame.width))
    top = max(0, int((box.top - padding) * frame.height))
    right = min(frame.width, max(left + 1, int((box.right + padding) * frame.width)))
    bottom = min(frame.height, max(top + 1, int((box.bottom + padding) * frame.height)))
    bytes_per_pixel = 4
    row_bytes = (right - left) * bytes_per_pixel
    source = frame.buffer_handle.readonly_view()
    payload = bytearray(row_bytes * (bottom - top))
    for row in range(bottom - top):
        start = (top + row) * frame.stride_bytes + left * bytes_per_pixel
        payload[row * row_bytes : (row + 1) * row_bytes] = source[start : start + row_bytes]
    return Frame(
        frame_id=f"{frame.frame_id}:crop:{left}:{top}:{right}:{bottom}",
        capture_timestamp=frame.capture_timestamp,
        present_estimate=frame.present_estimate,
        window_identity=frame.window_identity,
        width=right - left,
        height=bottom - top,
        stride_bytes=row_bytes,
        pixel_format=frame.pixel_format,
        physical_rect=frame.physical_rect,
        client_rect=frame.client_rect,
        source_backend=f"{frame.source_backend}:crop",
        buffer_handle=BufferHandle(
            f"{frame.buffer_handle.handle_id}:crop:{left}:{top}:{right}:{bottom}",
            BufferKind.CPU_BYTES,
            len(payload),
            bytes(payload),
        ),
    )


def select_target_regions(
    snapshot: PerceptionSnapshot, goal: str, *, limit: int = 2
) -> tuple[NormalizedBox, ...]:
    if limit < 0:
        raise ContractViolation("target crop limit cannot be negative")
    normalized_goal = normalize_visible_text(goal)
    scored: list[tuple[int, float, NormalizedBox]] = []
    for region in snapshot.visible_text:
        normalized = normalize_visible_text(region.text)
        overlap = int(bool(normalized and normalized in normalized_goal)) + int(
            bool(normalized_goal and normalized_goal in normalized)
        )
        token_overlap = sum(token in normalized_goal for token in region.text.casefold().split())
        scored.append((overlap * 10 + token_overlap, region.confidence, region.box))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return tuple(item[2] for item in scored[:limit] if item[0] > 0)


class GroundedVlmPlanner:
    """Strict one-decision vision planner; it never invents malformed actions."""

    def __init__(
        self,
        client: StructuredVisionClient,
        *,
        structured_output: bool = True,
        max_image_width: int = 1280,
        journal: DecisionJournal | None = None,
    ) -> None:
        if max_image_width < 320:
            raise ContractViolation("grounded planner image width must be at least 320")
        self._client = client
        self._structured_output = structured_output
        self._max_image_width = max_image_width
        self._journal = journal or NullJournal()
        self._schema_supported: bool | None = None
        self.last_raw_reply: str | None = None
        self.last_schema_valid = False

    @property
    def policy_version(self) -> str:
        return "grounded-vlm-1.0.0"

    def decide(
        self,
        *,
        snapshot: PerceptionSnapshot,
        frames: Sequence[Frame],
        goal: str,
        high_resolution_retry: bool = False,
    ) -> PlannerOutcome:
        started = time.monotonic()
        if not frames or frames[-1].frame_id != snapshot.frame_id:
            raise ContractViolation("grounded planner frames must end at the snapshot frame")
        images, temporal_count, crop_count = self._images(
            frames, snapshot, goal, high_resolution_retry
        )
        instruction = self._instruction(
            snapshot,
            goal,
            repair_reply=None,
            repair_error=None,
            temporal_count=temporal_count,
            crop_count=crop_count,
        )
        response_format = (
            GROUNDING_RESPONSE_FORMAT
            if self._structured_output and self._schema_supported is not False
            else None
        )
        try:
            reply = self._client.decide(
                images=images,
                instruction=instruction,
                response_format=response_format,
            )
            if response_format is not None:
                self._schema_supported = True
        except BackendUnavailableError as exc:
            if (
                response_format is None
                or "structured output unsupported" not in str(exc).casefold()
            ):
                raise
            self._schema_supported = False
            reply = self._client.decide(images=images, instruction=instruction)
        self.last_raw_reply = reply
        try:
            outcome = self._parse(reply, snapshot)
        except PlannerReplyError as exc:
            repair = self._client.decide(
                images=images,
                instruction=self._instruction(
                    snapshot,
                    goal,
                    repair_reply=reply,
                    repair_error=str(exc),
                    temporal_count=temporal_count,
                    crop_count=crop_count,
                ),
                response_format=(
                    GROUNDING_RESPONSE_FORMAT if self._schema_supported is True else None
                ),
            )
            self.last_raw_reply = repair
            try:
                outcome = self._parse(repair, snapshot)
            except PlannerReplyError:
                self.last_schema_valid = False
                outcome = self._abstain(
                    snapshot, "model reply remained invalid after one repair"
                )
                self._record(outcome, time.monotonic() - started)
                return outcome
        self.last_schema_valid = True
        self._record(outcome, time.monotonic() - started)
        return outcome

    def _record(self, outcome: PlannerOutcome, latency_s: float) -> None:
        action = outcome.action
        action_label = outcome.kind.value
        if action is not None:
            action_label = f"{action.kind.value}({action.target_label})"
        self._journal.record(
            DecisionRecord(
                timestamp=time.time(),
                kind="decision",
                latency_s=latency_s,
                action=action_label,
                detail=(
                    f"schema_valid={self.last_schema_valid}; confidence={outcome.confidence:.3f}; "
                    f"expected_effect={None if action is None else action.expected_effect}"
                ),
                quest=None,
                quest_step=outcome.explanation[:200] or None,
                images=None,
                reply_head=None if self.last_raw_reply is None else self.last_raw_reply[:500],
            )
        )

    def _images(
        self,
        frames: Sequence[Frame],
        snapshot: PerceptionSnapshot,
        goal: str,
        high_resolution_retry: bool,
    ) -> tuple[list[bytes], int, int]:
        latest = frames[-1]
        temporal = frames[-3:]
        images = [
            encode_frame_png(
                frame,
                max_width=(
                    frame.width
                    if high_resolution_retry and frame is latest
                    else self._max_image_width
                    if frame is latest
                    else min(self._max_image_width, 768)
                ),
            )
            for frame in temporal
        ]
        temporal_count = len(images)
        crop_count = 0
        for box in select_target_regions(snapshot, goal, limit=2):
            crop = crop_frame(latest, box, padding=0.04 if high_resolution_retry else 0.02)
            images.append(encode_frame_png(crop, max_width=max(crop.width, 32)))
            crop_count += 1
        return images, temporal_count, crop_count

    @staticmethod
    def _instruction(
        snapshot: PerceptionSnapshot,
        goal: str,
        repair_reply: str | None,
        repair_error: str | None,
        temporal_count: int,
        crop_count: int,
    ) -> str:
        ocr = "\n".join(
            f"- {region.text!r} bbox="
            f"[{region.box.left:.4f},{region.box.top:.4f},"
            f"{region.box.right:.4f},{region.box.bottom:.4f}]"
            f" confidence={region.confidence:.3f}"
            for region in snapshot.visible_text
        ) or "- OCR 未识别到可靠文本"
        repair = (
            "\n上一次回复未通过 Schema。只输出一个修正后的 JSON 对象，不得解释。"
            f"\n校验错误：{repair_error}"
            f"\n错误回复：{repair_reply[:1000]}"
            if repair_reply is not None
            else ""
        )
        return (
            "你是像素 GUI 闭环规划器。目标：" + goal + "\n"
            f"输入先给出 {temporal_count} 张按时间先后排列的干净全景图（最后一张最新），"
            f"随后给出 {crop_count} 张来自最新帧的 OCR/目标原分辨率裁剪。"
            "只能返回一个符合 JSON Schema 的决策。每次最多一个动作。"
            "必须先对照目标检查最新帧的完成证据；若可观察完成条件已经满足，必须输出"
            "kind=done、goal_status=succeeded、action=null，即使目标按钮因上一步成功而消失。"
            "只有确认目标尚未完成后，才能选择 act、wait 或 abstain。"
            "不要返回自由点击坐标；点击必须给出所见控件的 normalized target_bbox。"
            "同时检查文字和常见视觉图标；即使 OCR 没有标签，清晰可辨且与目标直接对应的"
            "图标（例如齿轮代表设置）也可以作为低风险目标，并为图标本体给出 bbox。"
            "目标要求打开某应用且对应图标已可见时，应输出 act，不要仅因图标无文字而 wait。"
            "加载时输出 wait，目标已完成时输出 done，不确定时输出 abstain。"
            "no_safe_action 只用于仔细检查全屏文字和图标后仍没有目标相关候选的情况。"
            "action 仅允许 click/key/hotkey；拖拽和多步序列由其他控制路径处理。"
            "kind=act 时 wait_reason 必须为 null 且 action 必须非 null；"
            "kind=wait 时 action 必须为 null；其他非 act 决策的 action 和 wait_reason "
            "都必须为 null。"
            "click 的 target_bbox 必须是四个归一化数且 key 必须为 null；"
            "key/hotkey 的 target_bbox 必须为 null 且 key 必须是已知语义键名。"
            "expected_effect 必须描述下一帧可验证的界面或文本变化。"
            "登录、删除、支付、发送、安装标记为 critical。\n"
            "当前 OCR：\n" + ocr + repair
        )

    @staticmethod
    def _parse(reply: str, snapshot: PerceptionSnapshot) -> PlannerOutcome:
        text = reply.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                text = "\n".join(lines[1:-1])
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PlannerReplyError("grounded reply is not one JSON object") from exc
        if not isinstance(payload, dict):
            raise PlannerReplyError("grounded reply JSON must be an object")
        try:
            kind = DecisionKind(str(payload["kind"]))
            goal_status = GoalStatus(str(payload["goal_status"]))
            confidence = float(payload["confidence"])
            scene_summary = str(payload["scene_summary"])
            visible_raw = payload["visible_text"]
            explanation = str(payload["explanation"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PlannerReplyError("grounded reply lacks required decision fields") from exc
        if not isinstance(visible_raw, list) or any(
            not isinstance(item, str) for item in visible_raw
        ):
            raise PlannerReplyError("grounded visible_text must be an array of strings")
        wait_raw = payload.get("wait_reason")
        try:
            wait_reason = None if wait_raw is None else WaitReason(str(wait_raw))
            action = GroundedVlmPlanner._parse_action(payload.get("action"))
            return PlannerOutcome(
                uuid.uuid4().hex,
                snapshot.frame_id,
                snapshot.frame_sequence,
                snapshot.window_identity.window_generation,
                snapshot.geometry_generation,
                snapshot.task_generation,
                kind,
                scene_summary,
                tuple(visible_raw),
                goal_status,
                confidence,
                action,
                wait_reason,
                explanation,
            )
        except (ContractViolation, ValueError) as exc:
            raise PlannerReplyError(f"invalid grounded decision: {exc}") from exc

    @staticmethod
    def _parse_action(value: object) -> GroundedAction | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise PlannerReplyError("grounded action must be an object or null")
        box_raw = value.get("target_bbox")
        box: NormalizedBox | None = None
        if box_raw is not None:
            if not isinstance(box_raw, list) or len(box_raw) != 4:
                raise PlannerReplyError("target_bbox must contain four normalized numbers")
            try:
                box = NormalizedBox(*(float(item) for item in box_raw))
            except (TypeError, ValueError, ContractViolation) as exc:
                raise PlannerReplyError("target_bbox is invalid") from exc
        try:
            key_raw = value.get("key")
            return GroundedAction(
                GuiActionKind(str(value["kind"])),
                str(value["target_label"]),
                box,
                str(value["expected_effect"]),
                float(value["confidence"]),
                ActionRisk(str(value["risk"])),
                None if key_raw is None else str(key_raw),
            )
        except (KeyError, TypeError, ValueError, ContractViolation) as exc:
            raise PlannerReplyError(f"invalid grounded action: {exc}") from exc

    @staticmethod
    def _abstain(snapshot: PerceptionSnapshot, explanation: str) -> PlannerOutcome:
        return PlannerOutcome(
            uuid.uuid4().hex,
            snapshot.frame_id,
            snapshot.frame_sequence,
            snapshot.window_identity.window_generation,
            snapshot.geometry_generation,
            snapshot.task_generation,
            DecisionKind.ABSTAIN,
            "unable to parse a reliable scene decision",
            snapshot.text,
            GoalStatus.UNKNOWN,
            0.0,
            explanation=explanation,
        )


VERIFIER_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "uga_action_verdict",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["approved", "confidence", "reason"],
            "properties": {
                "approved": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"},
            },
        },
    },
}


class GroundedOutcomeVerifier:
    """Optional larger-model cross-check used only for ambiguous decisions."""

    def __init__(self, client: StructuredVisionClient, *, threshold: float = 0.85) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ContractViolation("verifier confidence threshold must be in [0, 1]")
        self._client = client
        self._threshold = threshold

    def __call__(
        self,
        outcome: PlannerOutcome,
        snapshot: PerceptionSnapshot,
        frame: Frame,
        goal: str,
    ) -> bool:
        action = outcome.action
        instruction = (
            "独立复核以下 GUI 单步操作是否与屏幕和目标一致。仅返回 Schema JSON。\n"
            f"目标：{goal}\n"
            f"场景：{outcome.scene_summary}\n"
            f"OCR：{list(snapshot.text)}\n"
            f"候选动作：{None if action is None else action.target_label}\n"
            f"候选 bbox：{None if action is None else action.target_box}\n"
            f"预期效果：{None if action is None else action.expected_effect}"
        )
        try:
            reply = self._client.decide(
                images=[encode_frame_png(frame, max_width=1280)],
                instruction=instruction,
                response_format=VERIFIER_RESPONSE_FORMAT,
            )
            payload = json.loads(reply)
            return (
                isinstance(payload, dict)
                and payload.get("approved") is True
                and float(payload.get("confidence", -1)) >= self._threshold
                and isinstance(payload.get("reason"), str)
            )
        except (BackendUnavailableError, TypeError, ValueError, json.JSONDecodeError):
            return False
