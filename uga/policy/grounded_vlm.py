from __future__ import annotations

import json
import math
import time
import uuid
from collections.abc import Sequence
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Any, Protocol

from uga.agent.session_state import (
    close_glyph_aim,
    find_close_glyph,
    find_market_entry,
    find_xiuxian_path_quest_line,
    mumu_close_dialog_cancel,
    page_has_action_button,
    page_level_value,
    quest_is_market_task,
    quest_page_keyword,
    real_name_gate_active,
    realm_promotion_ready,
    stall_sell_item_cell,
    xiuxian_path_objective_goto,
)
from uga.agent.strategies import StrategyRegistry
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
    TextRegion,
    WaitReason,
)
from uga.policy.decision_journal import DecisionJournal, DecisionRecord, NullJournal
from uga.policy.structured_output import (
    strict_bounded_text,
    strict_confidence_value,
    strict_coordinates,
    strict_unit_interval_number,
)
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
                "kind": {
                    "enum": [item.value for item in DecisionKind],
                    "description": (
                        "DONE only when the positive external goal state is visibly "
                        "achieved; inability to continue is WAIT(no_safe_action), not DONE."
                    ),
                },
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
                                    ],
                                    "description": (
                                        "Must be JSON null for every click, including a "
                                        "visible Back/Return button. Only key/hotkey actions "
                                        "may use a semantic key name."
                                    ),
                                },
                            },
                        },
                    ]
                },
            },
        },
    },
}

COMPACT_GROUNDING_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "uga_compact_gui_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "confidence", "action", "wait_reason"],
            "properties": {
                "kind": {"enum": [item.value for item in DecisionKind]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "wait_reason": {
                    "anyOf": [
                        {"enum": [item.value for item in WaitReason]},
                        {"type": "null"},
                    ]
                },
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
                                "confidence",
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
                                "target_label": {"type": "string", "maxLength": 48},
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
                                "confidence": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 1,
                                },
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
        max_temporal_frames: int = 3,
        max_target_crops: int = 2,
        compact_output: bool = False,
        prefer_ocr_task_panel: bool = False,
        journal: DecisionJournal | None = None,
        required_goal_evidence: Sequence[str] = (),
        preferred_action_target: str | None = None,
        back_hotspot: tuple[float, float] | None = None,
        close_hotspot: tuple[float, float] | None = None,
        promote_hotspot: tuple[float, float] | None = None,
        strategy_registry: StrategyRegistry | None = None,
    ) -> None:
        if max_image_width < 320:
            raise ContractViolation("grounded planner image width must be at least 320")
        if not 1 <= max_temporal_frames <= 3:
            raise ContractViolation("grounded planner temporal frames must be within [1, 3]")
        if not 0 <= max_target_crops <= 2:
            raise ContractViolation("grounded planner target crops must be within [0, 2]")
        for name, hotspot in (
            ("back", back_hotspot),
            ("close", close_hotspot),
            ("promote", promote_hotspot),
        ):
            if hotspot is not None and (
                len(hotspot) != 2
                or not all(
                    math.isfinite(value) and 0.0 <= value <= 1.0 for value in hotspot
                )
            ):
                raise ContractViolation(
                    f"{name} hotspot must be two normalized coordinates"
                )
        evidence = tuple(value.strip() for value in required_goal_evidence)
        if any(not value for value in evidence) or len(evidence) != len(set(evidence)):
            raise ContractViolation("required goal evidence must be unique and non-empty")
        action_target = None if preferred_action_target is None else preferred_action_target.strip()
        if preferred_action_target is not None and not action_target:
            raise ContractViolation("preferred action target cannot be blank")
        self._client = client
        self._structured_output = structured_output
        self._max_image_width = max_image_width
        self._max_temporal_frames = max_temporal_frames
        self._max_target_crops = max_target_crops
        self._compact_output = compact_output
        self._prefer_ocr_task_panel = prefer_ocr_task_panel
        self._journal = journal or NullJournal()
        self._required_goal_evidence = evidence
        self._preferred_action_target = action_target
        self._back_hotspot = None if back_hotspot is None else tuple(back_hotspot)
        self._close_hotspot = None if close_hotspot is None else tuple(close_hotspot)
        self._promote_hotspot = (
            None if promote_hotspot is None else tuple(promote_hotspot)
        )
        self._task_panel_cooldown_s = 25.0
        self._last_task_panel_click: tuple[str, float] | None = None
        self._last_stall_item_click: float | None = None
        self._last_xiuxian_jump: float | None = None
        self._last_irrelevant_exit: float | None = None
        self._irrelevant_exit_prefer_close = False
        self._schema_supported: bool | None = None
        self.last_raw_reply: str | None = None
        self.last_schema_valid = False
        self.last_decision_was_dialogue = False
        self._login_agreement_clicked = False
        self._last_image_count = 0
        self._last_decision_source = "model"
        self.last_high_resolution_upgraded: bool | None = None
        # D12: the game-strategy registry scopes which decision fast paths
        # this planner may consult.  None is a documented legacy compat
        # default (unit tests) meaning "every known source"; production
        # always passes the profile's own registry — a generic profile
        # resolves to an EMPTY registry and none of the game rules fire.
        self._strategy_registry = strategy_registry

    @property
    def policy_version(self) -> str:
        return "grounded-vlm-1.0.0"

    @property
    def strategy_allows_fast_paths(self) -> bool:
        """D12: game fast paths fire only for a registered game strategy.

        ``None`` (legacy default) allows every known source; a registry —
        including an empty one for a generic profile — scopes the planner to
        its own inventory.
        """
        return self._strategy_registry is None or (
            self._strategy_registry.allows_fast_paths()
        )

    @property
    def last_decision_source(self) -> str:
        """Trusted, runtime-assigned origin of the latest decision.

        Read by the supervisor's action gate: model JSON never gets to claim
        this value — only rule code assigns ``ocr_*`` fast-path sources or
        ``model`` for a raw model reply.
        """
        return self._last_decision_source

    def decide(
        self,
        *,
        snapshot: PerceptionSnapshot,
        frames: Sequence[Frame],
        goal: str,
        high_resolution_retry: bool = False,
        preferred_action_available: bool = True,
        session_context: str | None = None,
        quest_target_level: int | None = None,
        quest_text: str | None = None,
    ) -> PlannerOutcome:
        started = time.monotonic()
        if not frames or frames[-1].frame_id != snapshot.frame_id:
            raise ContractViolation("grounded planner frames must end at the snapshot frame")
        fast_outcome = self._ocr_fast_path(
            snapshot,
            quest_target_level=quest_target_level,
            quest_text=quest_text,
        )
        if fast_outcome is not None:
            # Deterministic, freshly grounded controls should not wait behind a slow
            # local VLM request.  The closed-loop supervisor still revalidates the
            # target on the newest frame immediately before physical execution.
            self.last_raw_reply = None
            self.last_schema_valid = True
            self._last_image_count = 0
            self._record(fast_outcome, time.monotonic() - started)
            return fast_outcome
        images, temporal_count, crop_count = self._images(
            frames, snapshot, goal, high_resolution_retry
        )
        self._last_image_count = len(images)
        preferred_target = (
            self._preferred_action_target if preferred_action_available else None
        )
        consumed_target = (
            self._preferred_action_target if not preferred_action_available else None
        )
        instruction = (
            self._compact_instruction(
                snapshot,
                goal,
                preferred_action_target=preferred_target,
                consumed_action_target=consumed_target,
                session_context=session_context,
            )
            if self._compact_output
            else self._instruction(
                snapshot,
                goal,
                repair_reply=None,
                repair_error=None,
                temporal_count=temporal_count,
                crop_count=crop_count,
                required_goal_evidence=self._required_goal_evidence,
                preferred_action_target=preferred_target,
                consumed_action_target=consumed_target,
                session_context=session_context,
            )
        )
        response_format = (
            (
                COMPACT_GROUNDING_RESPONSE_FORMAT
                if self._compact_output
                else GROUNDING_RESPONSE_FORMAT
            )
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
            outcome = self._parse(reply, snapshot, compact=self._compact_output)
            outcome = self._apply_task_panel_fallback(
                outcome, snapshot, quest_text=quest_text
            )
            outcome = self._snap_model_action_to_ocr(outcome, snapshot)
        except PlannerReplyError as exc:
            repair = self._client.decide(
                images=images,
                instruction=(
                    self._compact_instruction(
                        snapshot,
                        goal,
                        preferred_action_target=preferred_target,
                        consumed_action_target=consumed_target,
                        repair_error=str(exc),
                        session_context=session_context,
                    )
                    if self._compact_output
                    else self._instruction(
                        snapshot,
                        goal,
                        repair_reply=reply,
                        repair_error=str(exc),
                        temporal_count=temporal_count,
                        crop_count=crop_count,
                        required_goal_evidence=self._required_goal_evidence,
                        preferred_action_target=preferred_target,
                        consumed_action_target=consumed_target,
                        session_context=session_context,
                    )
                ),
                response_format=(
                    (
                        COMPACT_GROUNDING_RESPONSE_FORMAT
                        if self._compact_output
                        else GROUNDING_RESPONSE_FORMAT
                    )
                    if self._schema_supported is True
                    else None
                ),
            )
            self.last_raw_reply = repair
            try:
                outcome = self._parse(repair, snapshot, compact=self._compact_output)
                outcome = self._apply_task_panel_fallback(
                    outcome, snapshot, quest_text=quest_text
                )
                outcome = self._snap_model_action_to_ocr(outcome, snapshot)
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

    def _snap_model_action_to_ocr(
        self,
        outcome: PlannerOutcome,
        snapshot: PerceptionSnapshot,
    ) -> PlannerOutcome:
        """Replace a loose model box with the latest matching OCR text box."""
        action = outcome.action
        if (
            self._last_decision_source != "model"
            or action is None
            or action.target_box is None
            or action.kind not in {GuiActionKind.CLICK, GuiActionKind.LONG_CLICK}
        ):
            return outcome
        target = normalize_visible_text(action.target_label)
        if len(target) < 2:
            return outcome
        matches: list[tuple[float, float, TextRegion]] = []
        for region in snapshot.visible_text:
            text = normalize_visible_text(region.text)
            if len(text) < 2 or region.confidence < 0.5:
                continue
            similarity = SequenceMatcher(None, target, text).ratio()
            contained = target in text or text in target
            if not contained and (min(len(target), len(text)) < 4 or similarity < 0.72):
                continue
            matches.append((1.0 if target == text else similarity, region.confidence, region))
        if not matches:
            return outcome
        candidate = max(matches, key=lambda item: (item[0], item[1]))[2]
        self._last_decision_source = "model_ocr_snap"
        return replace(
            outcome,
            action=replace(
                action,
                target_box=candidate.box,
                confidence=min(action.confidence, candidate.confidence),
            ),
            explanation=f"{outcome.explanation}; target snapped to latest matching OCR box",
        )

    def _ocr_fast_path(
        self,
        snapshot: PerceptionSnapshot,
        *,
        quest_target_level: int | None = None,
        quest_text: str | None = None,
    ) -> PlannerOutcome | None:
        if real_name_gate_active(snapshot.visible_text):
            # 实名登记表单（姓名/证件号=个人身份信息）：代理绝不代填也不
            # 点击，直接挂起等待 owner 完成登记，不耗一次 VLM 推理。
            self.last_raw_reply = None
            self.last_schema_valid = True
            self._last_image_count = 0
            self._last_decision_source = "real_name_gate_standby"
            return PlannerOutcome(
                uuid.uuid4().hex,
                snapshot.frame_id,
                snapshot.frame_sequence,
                snapshot.window_identity.window_generation,
                snapshot.geometry_generation,
                snapshot.task_generation,
                DecisionKind.WAIT,
                "real-name registration form",
                snapshot.text,
                GoalStatus.IN_PROGRESS,
                1.0,
                None,
                WaitReason.NO_SAFE_ACTION,
                explanation="real-name registration gate: standing by for the owner",
            )
        if not self._prefer_ocr_task_panel:
            return None
        if not self.strategy_allows_fast_paths:
            # D12: a generic profile carries no game rule inventory — every
            # game-specific fast path below is disabled and the decision
            # falls through to the vision model.
            return None
        dialog_cancel = mumu_close_dialog_cancel(snapshot.visible_text)
        if dialog_cancel is not None:
            # MuMu 自己的"确定要关闭"确认框挡住整个窗口：唯一安全处置是
            # 取消（确定会关掉模拟器、杀掉整个 run）。规则层直接接管，
            # 不消耗 VLM 推理，也绝不允许模型碰这个弹窗的确定按钮。
            self._last_decision_source = "ocr_mumu_dialog_cancel_fast"
            return self._ocr_action(
                snapshot,
                dialog_cancel,
                source="ocr_mumu_dialog_cancel_fast",
                expected_effect=(
                    "the MuMu close confirmation is dismissed and the emulator stays open"
                ),
                action_kind=GuiActionKind.CLICK,
            )
        dialogue_advance_cue = next(
            (
                region
                for region in snapshot.visible_text
                if "秒后自动继续" in normalize_visible_text(region.text)
                or "回顾剧情" in normalize_visible_text(region.text)
            ),
            None,
        )
        if dialogue_advance_cue is not None and "秒后自动继续" in normalize_visible_text(
            dialogue_advance_cue.text
        ):
            # 对话快速跳过：倒计时区就是推进区——用鼠标连点右下角（比按
            # Space 可靠，过场剧情不吃键盘），并进入对话快速连点节奏。
            self.last_decision_was_dialogue = True
            self._last_decision_source = "ocr_dialogue_click_fast"
            return self._ocr_action(
                snapshot,
                dialogue_advance_cue,
                source="ocr_dialogue_click_fast",
                expected_effect="the dialogue advances to the next line",
                action_kind=GuiActionKind.CLICK,
            )
        self.last_decision_was_dialogue = False
        login_start = next(
            (r for r in snapshot.visible_text if "开始游戏" in r.text), None
        )
        agreement = next(
            (r for r in snapshot.visible_text if "同意用户协议" in r.text), None
        )
        if login_start is not None and agreement is not None:
            if not self._login_agreement_clicked:
                # 游戏登录页：必须先勾选同意用户协议，开始游戏才会生效。
                self._login_agreement_clicked = True
                self._last_decision_source = "ocr_login_agreement_fast"
                return self._ocr_action(
                    snapshot,
                    agreement,
                    source="ocr_login_agreement_fast",
                    expected_effect="the user agreement checkbox is ticked",
                    action_kind=GuiActionKind.CLICK,
                    pointer_offset=(-0.06, 0.0),
                )
        elif self._login_agreement_clicked:
            self._login_agreement_clicked = False
        glyph = find_close_glyph(snapshot.visible_text)
        if (
            glyph is not None
            and not page_has_action_button(snapshot.visible_text)
        ):
            # A visible × glyph dismisses a popup — but only when the page has
            # NO actionable button (上架/确定/领取…).  A form with buttons is a
            # workflow the model must operate, not a popup to dismiss; closing
            # it would kill the very flow the quest needs.
            return self._close_glyph_action(snapshot, glyph[0], glyph[1])
        xiuxian_line = find_xiuxian_path_quest_line(snapshot.visible_text)
        if xiuxian_line is not None:
            now_mono = time.monotonic()
            if (
                self._last_xiuxian_jump is None
                or now_mono - self._last_xiuxian_jump > 10.0
            ):
                # 修仙之路任务：点左上角任务面板的任务行本身就会自动跳转到
                # 对应界面（owner 确认）。模型反复点"主线"栏目标题不开任务
                # 行，规则层直接点真实任务行触发跳转，10s 冷却防连点。
                self._last_xiuxian_jump = now_mono
                return self._ocr_action(
                    snapshot,
                    xiuxian_line,
                    source="ocr_xiuxian_path_jump_fast",
                    expected_effect=(
                        "the game auto-navigates to the 修仙之路 objective interface"
                    ),
                    action_kind=GuiActionKind.CLICK,
                )
        objective_goto = xiuxian_path_objective_goto(snapshot.visible_text)
        if objective_goto is not None:
            now_mono = time.monotonic()
            if (
                self._last_xiuxian_jump is None
                or now_mono - self._last_xiuxian_jump > 10.0
            ):
                # 修仙之路界面内：目标文字本身不可点（模型点它永远无效），
                # 可点的是每个目标行右下方的"前往"按钮——规则层直接点最
                # 上方未完成目标行的前往，共享 10s 跳转冷却防连点。
                self._last_xiuxian_jump = now_mono
                return self._ocr_action(
                    snapshot,
                    objective_goto,
                    source="ocr_xiuxian_objective_goto_fast",
                    expected_effect=(
                        "the game navigates to the selected 修仙之路 objective"
                    ),
                    action_kind=GuiActionKind.CLICK,
                )
        if (
            quest_is_market_task(quest_text)
            and self._back_hotspot is not None
        ):
            item_cell = stall_sell_item_cell(snapshot.visible_text)
            now_mono = time.monotonic()
            if (
                item_cell is not None
                and (
                    self._last_stall_item_click is None
                    or now_mono - self._last_stall_item_click > 8.0
                )
            ):
                # 摆摊出售页：点物品格子（图标在等级标签上方）打开出售对话框。
                self._last_stall_item_click = now_mono
                return self._ocr_action(
                    snapshot,
                    item_cell,
                    source="ocr_stall_item_fast",
                    expected_effect="the item sell dialog opens",
                    action_kind=GuiActionKind.CLICK,
                    pointer_offset=(0.0, -0.045),
                )
        if (
            self._promote_hotspot is not None
            and realm_promotion_ready(snapshot.visible_text)
        ):
            # 境界页目标全部已完成：晋升奖章是图形控件 OCR 读不出，规则层
            # 直接点击已校准的奖章热点推进境界。
            promote_x, promote_y = self._promote_hotspot
            return self._hotspot_click_action(
                snapshot, "ui_promote", (promote_x, promote_y),
                "ocr_realm_promote_fast",
                "the realm promotes and the objective progress resets",
            )
        if quest_is_market_task(quest_text) and self._back_hotspot is not None:
            # 市场类任务（出售/摆摊/购买）：点任务面板文字没有用——需要通过
            # 市场界面完成。游戏会用高亮标记市场入口；OCR 可见时直接点击，
            # 不可见时交给模型找（提示词已说明高亮标记优先）。
            market = find_market_entry(snapshot.visible_text)
            if market is not None:
                self._last_decision_source = "ocr_market_entry_fast"
                return self._ocr_action(
                    snapshot,
                    market,
                    source="ocr_market_entry_fast",
                    expected_effect="the market interface opens",
                    action_kind=GuiActionKind.CLICK,
                )
        if quest_target_level is not None and self._back_hotspot is not None:
            # Rule-based quest-step planning: when the tracked quest demands a
            # level that the page already shows as reached, the step is done
            # and further upgrade clicks only waste materials.  Leave the page
            # through the calibrated visual back control.
            page_level = page_level_value(snapshot.visible_text)
            if page_level is not None and page_level >= quest_target_level:
                return self._exit_action(snapshot, "ocr_quest_satisfied_back_fast")
        candidate = self._task_panel_candidate(snapshot)
        if candidate is not None and quest_is_market_task(quest_text):
            # 市场类任务点任务面板文字只会打开任务详情，推进不了任务。
            candidate = None
        source = "ocr_task_panel_fast"
        expected_effect = "open or auto-navigate the visible tracked task"
        action_kind = GuiActionKind.CLICK
        pointer_offset = (0.0, 0.0)
        if candidate is not None and not self._task_panel_click_allowed(candidate):
            candidate = None
        if candidate is None:
            candidate = self._progress_control_candidate(snapshot)
            source = "ocr_progress_control_fast"
            expected_effect = "advance the visible dialogue or guided flow"
            action_kind = self._progress_action_kind(candidate)
        if candidate is None and self._back_hotspot is not None:
            keyword = quest_page_keyword(quest_text)
            if (
                keyword is not None
                and keyword
                not in " ".join(
                    normalize_visible_text(region.text)
                    for region in snapshot.visible_text
                )
                and len(snapshot.visible_text) >= 4
            ):
                # The tracked quest is about a specific game system (灵宠…)
                # that this settled feature page never mentions: the page
                # cannot serve the quest, so leave it instead of letting the
                # model grind irrelevant upgrade buttons.  The exit is
                # cooldown-bounded (a modal that swallows the hotspot click
                # must not spin this path forever — the model gets its turn)
                # and alternates back/close so one dead exit control cannot
                # stall the page leave.
                now_mono = time.monotonic()
                if (
                    self._last_irrelevant_exit is not None
                    and now_mono - self._last_irrelevant_exit <= 20.0
                ):
                    return None
                self._last_irrelevant_exit = now_mono
                prefer_close = self._irrelevant_exit_prefer_close
                self._irrelevant_exit_prefer_close = not prefer_close
                return self._exit_action(
                    snapshot,
                    "ocr_quest_irrelevant_back_fast",
                    prefer_close=prefer_close,
                )
        if candidate is None and self._completed_pet_panel(snapshot):
            # A completed pet panel has no useful in-page control. Leave the
            # feature page through the calibrated visual back control instead
            # of guessing a title-anchored offset click.
            if self._back_hotspot is not None:
                return self._exit_action(snapshot, "ocr_completed_panel_back_fast")
            return None
        if candidate is None:
            return None
        self._last_decision_source = source
        return self._ocr_action(
            snapshot,
            candidate,
            source=source,
            expected_effect=expected_effect,
            action_kind=action_kind,
            pointer_offset=pointer_offset,
        )

    def _task_panel_click_allowed(self, candidate: TextRegion) -> bool:
        """Cooldown for tracker clicks: once a click for this quest text
        landed, hand control back to the model — the destination page's
        guidance (highlighted buttons) is the real next step, and re-clicking
        the tracker forever starves the model."""
        now = time.monotonic()
        text = normalize_visible_text(candidate.text)
        last = self._last_task_panel_click
        if (
            last is not None
            and last[0] == text
            and now - last[1] < self._task_panel_cooldown_s
        ):
            return False
        self._last_task_panel_click = (text, now)
        return True

    def refresh_task_panel_cooldown(self) -> None:
        """Restart the tracker-click cooldown (called after an exit escape):
        the agent must re-observe the world page instead of being dragged
        straight back into the page it just left."""
        if self._last_task_panel_click is not None:
            self._last_task_panel_click = (
                self._last_task_panel_click[0],
                time.monotonic(),
            )

    def _close_glyph_action(
        self, snapshot: PerceptionSnapshot, region: TextRegion, aim_right: bool
    ) -> PlannerOutcome:
        del aim_right  # the aim offset is folded into the pointer offset below
        aim_x, aim_y = close_glyph_aim(region)
        center = region.box.center
        self._last_decision_source = "ocr_close_glyph_fast"
        return PlannerOutcome(
            uuid.uuid4().hex,
            snapshot.frame_id,
            snapshot.frame_sequence,
            snapshot.window_identity.window_generation,
            snapshot.geometry_generation,
            snapshot.task_generation,
            DecisionKind.ACT,
            "a dismissible popup close glyph is visible",
            snapshot.text,
            GoalStatus.IN_PROGRESS,
            region.confidence,
            GroundedAction(
                GuiActionKind.CLICK,
                "ui_close",
                region.box,
                "the popup closes and the underlying page becomes visible",
                region.confidence,
                ActionRisk.LOW,
                pointer_offset_x=max(-0.25, min(0.25, aim_x - center.x)),
                pointer_offset_y=max(-0.25, min(0.25, aim_y - center.y)),
            ),
            explanation=(
                "ocr_close_glyph_fast clicked the visible × glyph "
                f"{region.text!r}"
            ),
        )

    def _hotspot_click_action(
        self,
        snapshot: PerceptionSnapshot,
        label: str,
        hotspot: tuple[float, float],
        source: str,
        expected_effect: str,
    ) -> PlannerOutcome:
        x, y = hotspot
        left = max(0.0, x - 0.035)
        top = max(0.0, y - 0.030)
        right = min(1.0, max(left + 0.01, x + 0.035))
        bottom = min(1.0, max(top + 0.01, y + 0.030))
        self._last_decision_source = source
        return PlannerOutcome(
            uuid.uuid4().hex,
            snapshot.frame_id,
            snapshot.frame_sequence,
            snapshot.window_identity.window_generation,
            snapshot.geometry_generation,
            snapshot.task_generation,
            DecisionKind.ACT,
            expected_effect,
            snapshot.text,
            GoalStatus.IN_PROGRESS,
            1.0,
            GroundedAction(
                GuiActionKind.CLICK,
                label,
                NormalizedBox(left, top, right, bottom),
                expected_effect,
                1.0,
                ActionRisk.LOW,
            ),
            explanation=f"{source} clicked the calibrated {label} hotspot",
        )

    def _exit_action(
        self, snapshot: PerceptionSnapshot, source: str, *, prefer_close: bool = False
    ) -> PlannerOutcome:
        hotspot = (
            self._close_hotspot
            if prefer_close and self._close_hotspot is not None
            else self._back_hotspot
            if self._back_hotspot is not None
            else self._close_hotspot
        )
        label = "ui_close" if hotspot == self._close_hotspot else "ui_back"
        assert hotspot is not None
        x, y = hotspot
        left = max(0.0, x - 0.035)
        top = max(0.0, y - 0.030)
        right = min(1.0, max(left + 0.01, x + 0.035))
        bottom = min(1.0, max(top + 0.01, y + 0.030))
        self._last_decision_source = source
        return PlannerOutcome(
            uuid.uuid4().hex,
            snapshot.frame_id,
            snapshot.frame_sequence,
            snapshot.window_identity.window_generation,
            snapshot.geometry_generation,
            snapshot.task_generation,
            DecisionKind.ACT,
            "feature page finished; leaving through the calibrated exit control",
            snapshot.text,
            GoalStatus.IN_PROGRESS,
            1.0,
            GroundedAction(
                GuiActionKind.CLICK,
                label,
                NormalizedBox(left, top, right, bottom),
                "the feature page closes and the world view with the main-quest "
                "tracker reappears",
                1.0,
                ActionRisk.LOW,
            ),
            explanation=f"{source} clicked the calibrated {label} hotspot",
        )

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
                    f"source={self._last_decision_source}; "
                    f"schema_valid={self.last_schema_valid}; confidence={outcome.confidence:.3f}; "
                    f"expected_effect={None if action is None else action.expected_effect}"
                ),
                quest=None,
                quest_step=outcome.explanation[:200] or None,
                images=self._last_image_count,
                reply_head=None if self.last_raw_reply is None else self.last_raw_reply[:500],
            )
        )

    def _apply_task_panel_fallback(
        self,
        outcome: PlannerOutcome,
        snapshot: PerceptionSnapshot,
        *,
        quest_text: str | None = None,
    ) -> PlannerOutcome:
        self._last_decision_source = "model"
        if not self._prefer_ocr_task_panel or outcome.kind == DecisionKind.DONE:
            return outcome
        if not self.strategy_allows_fast_paths:
            # D12: a generic profile carries no game rule inventory — the
            # model's own reply stands without rule-level substitution.
            return outcome
        if quest_is_market_task(quest_text):
            # 市场类任务：任务面板文字不是可操作按钮，fallback 不得把它
            # 塞回给监督器（那会重建无限点击循环）。
            return outcome
        candidate = self._task_panel_candidate(snapshot)
        source = "ocr_task_panel"
        expected_effect = "open or auto-navigate the visible tracked task"
        action_kind = GuiActionKind.CLICK
        if candidate is not None and not self._task_panel_click_allowed(candidate):
            # The tracker was just clicked for this quest text: re-clicking it
            # forever starves the model, so respect the cooldown here too.
            candidate = None
        if candidate is None:
            candidate = self._progress_control_candidate(snapshot)
            source = "ocr_progress_control"
            expected_effect = "advance the visible dialogue or guided flow"
            action_kind = self._progress_action_kind(candidate)
        if candidate is None:
            return outcome
        if outcome.action is not None and outcome.action.target_box is not None:
            center = outcome.action.target_box.center
            if center.x <= 0.28 and 0.20 <= center.y <= 0.38:
                return outcome
        if outcome.kind not in {
            DecisionKind.ACT,
            DecisionKind.WAIT,
            DecisionKind.ABSTAIN,
            DecisionKind.RECOVER,
        }:
            return outcome
        self._last_decision_source = source
        return self._ocr_action(
            snapshot,
            candidate,
            source=source,
            expected_effect=expected_effect,
            action_kind=action_kind,
        )

    @staticmethod
    def _ocr_action(
        snapshot: PerceptionSnapshot,
        candidate: TextRegion,
        *,
        source: str,
        expected_effect: str,
        action_kind: GuiActionKind,
        pointer_offset: tuple[float, float] = (0.0, 0.0),
    ) -> PlannerOutcome:
        confidence = candidate.confidence
        return PlannerOutcome(
            uuid.uuid4().hex,
            snapshot.frame_id,
            snapshot.frame_sequence,
            snapshot.window_identity.window_generation,
            snapshot.geometry_generation,
            snapshot.task_generation,
            DecisionKind.ACT,
            "OCR task tracker",
            snapshot.text,
            GoalStatus.IN_PROGRESS,
            confidence,
            GroundedAction(
                action_kind,
                candidate.text,
                candidate.box,
                expected_effect,
                confidence,
                ActionRisk.LOW,
                pointer_offset_x=pointer_offset[0],
                pointer_offset_y=pointer_offset[1],
            ),
            explanation=f"{source} selected a freshly grounded deterministic control",
        )

    @staticmethod
    def _progress_action_kind(candidate: TextRegion | None) -> GuiActionKind:
        if candidate is None:
            return GuiActionKind.CLICK
        label = normalize_visible_text(candidate.text)
        if any(term in label for term in ("传功", "疗伤", "按住", "长按")):
            return GuiActionKind.LONG_CLICK
        return GuiActionKind.CLICK

    @staticmethod
    def _task_panel_candidate(snapshot: PerceptionSnapshot) -> TextRegion | None:
        excluded = {
            "任务",
            "主线",
            "世界",
            "仙遇",
            "经验",
            "追踪",
        }
        has_main_quest_header = any(
            normalize_visible_text(region.text) == "主线"
            and region.confidence > 0.80
            and region.box.center.x <= 0.28
            and 0.10 <= region.box.center.y <= 0.32
            for region in snapshot.visible_text
        )
        if not has_main_quest_header:
            return None
        candidates = []
        for region in snapshot.visible_text:
            label = normalize_visible_text(region.text)
            center = region.box.center
            if (
                region.confidence <= 0.85
                or not label
                or label in excluded
                or len(label) < 2
                or center.x > 0.28
                or not 0.20 <= center.y <= 0.38
            ):
                continue
            candidates.append(region)
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda region: (
                len(normalize_visible_text(region.text)),
                region.box.right - region.box.left,
                region.confidence,
            ),
        )

    @staticmethod
    def _progress_control_candidate(snapshot: PerceptionSnapshot) -> TextRegion | None:
        direct_terms = (
            "提交",
            "跳过",
            "继续",
            "下一步",
            "确定",
            "上阵",
            "领取奖励",
            "领取",
            "传功",
            "疗伤",
        )
        direct = [
            region
            for region in snapshot.visible_text
            if region.confidence > 0.85
            and len(normalize_visible_text(region.text)) <= 10
            and any(
                (
                    normalize_visible_text(region.text) == term
                    if term == "上阵"
                    else term in normalize_visible_text(region.text)
                )
                for term in direct_terms
            )
        ]
        if direct:
            return max(direct, key=lambda region: region.confidence)
        # Generic bottom-of-screen text is not sufficient evidence of dialogue.
        # Upgrade, pet, and summon panels use the same visual arrangement for
        # passive labels, and clicking those labels creates a no-progress loop.
        # Explicit dialogue cues are handled by _ocr_fast_path; otherwise the
        # single-frame vision model grounds the next action.
        return None

    @staticmethod
    def _completed_pet_panel(
        snapshot: PerceptionSnapshot,
    ) -> bool:
        """Detect a pet panel whose work is genuinely finished.

        Only strong terminal signals count: the evolution-limit banner or a
        formation panel whose main slot is filled.  A level like ``3/40`` is
        NOT completion evidence — during a level-up quest (达到10级) the page
        must be worked, not left.
        """
        normalized = tuple(
            (region, normalize_visible_text(region.text))
            for region in snapshot.visible_text
        )
        pet_titles = [
            region
            for region, text in normalized
            if text == "灵宠" and region.confidence > 0.85
        ]
        reached_evolution_limit = any(
            "已培养至进化上限" in text for _, text in normalized
        )
        formation_panel = any(text == "主战位" for _, text in normalized) and any(
            text == "辅助位" for _, text in normalized
        )
        main_slot_empty = any(
            "未上阵" in text and region.box.center.x <= 0.28
            for region, text in normalized
        )
        main_slot_occupied = formation_panel and not main_slot_empty and any(
            "修为" in text and region.box.center.x <= 0.32
            for region, text in normalized
        )
        return bool(pet_titles) and (
            reached_evolution_limit or main_slot_occupied
        )

    def _images(
        self,
        frames: Sequence[Frame],
        snapshot: PerceptionSnapshot,
        goal: str,
        high_resolution_retry: bool,
    ) -> tuple[list[bytes], int, int]:
        latest = frames[-1]
        temporal = frames[-self._max_temporal_frames :]
        # F14: a high-resolution recovery must actually raise the effective
        # pixel budget of the decision frame — widening crop padding alone
        # changed nothing when no crops were configured.  The overview
        # doubles up to the 1280 hard cap; at the cap the retry is recorded
        # as a non-upgrade instead of silently re-sending identical pixels.
        overview_width = self._max_image_width
        if high_resolution_retry:
            overview_width = min(overview_width * 2, 1280)
        self.last_high_resolution_upgraded = (
            high_resolution_retry and overview_width > self._max_image_width
        )
        images = [
            encode_frame_png(
                frame,
                max_width=(
                    overview_width
                    if frame is latest
                    else min(self._max_image_width, 768)
                ),
            )
            for frame in temporal
        ]
        temporal_count = len(images)
        crop_count = 0
        for box in select_target_regions(
            snapshot, goal, limit=self._max_target_crops
        ):
            crop = crop_frame(latest, box, padding=0.04 if high_resolution_retry else 0.02)
            images.append(encode_frame_png(crop, max_width=max(crop.width, 32)))
            crop_count += 1
        return images, temporal_count, crop_count

    @staticmethod
    def _compact_instruction(
        snapshot: PerceptionSnapshot,
        goal: str,
        *,
        preferred_action_target: str | None = None,
        consumed_action_target: str | None = None,
        repair_error: str | None = None,
        session_context: str | None = None,
    ) -> str:
        ocr = "\n".join(
            f"- {region.text!r} [{region.box.left:.3f},{region.box.top:.3f},"
            f"{region.box.right:.3f},{region.box.bottom:.3f}]"
            for region in snapshot.visible_text[:24]
        ) or "- 无"
        preference = (
            f"\n优先动作文字：{preferred_action_target}"
            if preferred_action_target is not None
            else ""
        )
        consumed = (
            f"\n禁止重复点击已完成目标：{consumed_action_target}"
            if consumed_action_target is not None
            else ""
        )
        repair = f"\n上次格式错误：{repair_error}" if repair_error else ""
        session = f"\n跨帧记忆：\n{session_context}" if session_context else ""
        return (
            "你是游戏 GUI 操作器。看这张截图，输出一个 JSON 动作来推进目标。\n"
            f"总目标：{goal}\n"
            f"OCR（文字和归一化框）：\n{ocr}"
            f"{preference}{consumed}{repair}{session}\n"
            "做法：找到画面里最能推进目标的按钮或文字，直接输出对它的点击，"
            "没有文字的图形按钮也照样点（target_label 用简短描述）。"
            "游戏常用高亮/发光边框标记下一步要点的按钮——优先点击被高亮标记的元素，"
            "而不是反复点击左上角的任务面板文字（那只是任务说明，不是按钮）。"
            "上架/出售类操作成功后（物品已出现在出售列表中），立即点击返回或×退出界面，"
            "不要重复上架同一物品；出售需要其他玩家购买，退出后点击任务追踪查看进度即可。\n"
            "仅有的禁止项：不要点击 充值/首充/礼包/支付 类内容；"
            "不要点击滚动公告；同一按钮点击后画面没变化就不要再点它；"
            "截图最顶部的 MuMu 模拟器标题栏（窗口按钮/标签页/×）不是游戏内容，绝不点击。\n"
            "ACT：kind=act, action={kind:click, target_label, target_bbox, "
            "confidence, key:null}。"
            "示例：{\"kind\":\"act\",\"confidence\":0.9,\"action\":{\"kind\":\"click\","
            "\"target_label\":\"挑战\",\"target_bbox\":[0.4,0.5,0.6,0.6],"
            "\"confidence\":0.9,\"key\":null},\"wait_reason\":null}"
        )

    @staticmethod
    def _instruction(
        snapshot: PerceptionSnapshot,
        goal: str,
        repair_reply: str | None,
        repair_error: str | None,
        temporal_count: int,
        crop_count: int,
        required_goal_evidence: Sequence[str] = (),
        preferred_action_target: str | None = None,
        consumed_action_target: str | None = None,
        session_context: str | None = None,
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
        normalized_ocr = tuple(
            normalize_visible_text(region.text) for region in snapshot.visible_text
        )
        missing_evidence = tuple(
            value
            for value in required_goal_evidence
            if not any(normalize_visible_text(value) in item for item in normalized_ocr)
        )
        required = ""
        if required_goal_evidence:
            required = (
                "\n显式完成证据（指最新 OCR 中的字面文字，不接受语义近似或推断）：\n"
                + "\n".join(f"- {value}" for value in required_goal_evidence)
                + "\n当前缺失证据："
                + (", ".join(missing_evidence) if missing_evidence else "无")
                + (
                    "。因此当前帧绝对禁止 DONE。"
                    if missing_evidence
                    else "。全部显式证据已出现；当前帧必须输出 DONE，绝对禁止 ACT。"
                )
            )
        target_visible = bool(
            preferred_action_target
            and any(
                normalize_visible_text(preferred_action_target) in item
                for item in normalized_ocr
            )
        )
        action_target = ""
        if preferred_action_target is not None:
            action_target = (
                f"\n本任务的单步导航目标是：{preferred_action_target}。"
                "若输出 ACT，target_label 必须是该目标，不得点击目标页内的其他选项。"
                + (
                    "该目标文字已在最新 OCR 中出现且完成证据仍缺失；当前帧必须输出 "
                    "ACT，target_label 使用该文字，bbox 对准它所在的整行可点击区域。"
                    if target_visible and missing_evidence
                    else "仅在它真实可见且完成证据缺失时点击。"
                )
            )
        if consumed_action_target is not None:
            action_target = (
                f"\n单步导航目标 {consumed_action_target} 已执行且已观测到界面效果。"
                "禁止再次点击该目标或目标页标题；若完成证据仍缺失，输出 WAIT(no_safe_action) "
                "或 ABSTAIN，不得用重复点击代替缺失证据。"
            )
        session = f"\n跨帧会话记忆：\n{session_context}" if session_context else ""
        policy = (
            "\n页面策略：充值/首充/礼包/特惠类促销弹窗绝对不要点击其中任何文字或按钮；"
            "功能页中任务目标已达成或没有下一步时，输出 ACT 点击返回/关闭控件退出页面；"
            "点击过但画面无变化的按钮禁止再次点击。"
        )
        return (
            "你是像素 GUI 闭环规划器。目标：" + goal + "\n"
            f"输入先给出 {temporal_count} 张按时间先后排列的干净全景图（最后一张最新），"
            f"随后给出 {crop_count} 张来自最新帧的 OCR/目标原分辨率裁剪。"
            "只能返回一个符合 JSON Schema 的决策。每次最多一个动作。"
            "必须先对照目标检查最新帧的完成证据；若可观察完成条件已经满足，必须输出"
            "kind=done、goal_status=succeeded、action=null，即使目标按钮因上一步成功而消失。"
            "DONE 只表示目标要求的正向外部状态已在画面中可见实现；"
            "目标尚未完成时绝对禁止 DONE，继续输出 act 推进目标。"
            "目标中的每一项完成条件都必须满足；仅看到通往目标页的导航行不代表已打开目标页。"
            "visible_text 只能抄录最新图像或 OCR 中真实存在的文字，禁止写入期望但未出现的文字。"
            "若目标要求 Open/打开某项，且同名或明确同义的可点击行在最新帧可见，"
            "应对该行输出 act。"
            "不要返回自由点击坐标；点击必须给出所见控件的 normalized target_bbox。"
            "同时检查文字和常见视觉图标；即使 OCR 没有标签，清晰可辨且与目标直接对应的"
            "图标（例如齿轮代表设置）也可以作为低风险目标，并为图标本体给出 bbox。"
            "action 仅允许 click/key/hotkey；拖拽和多步序列由其他控制路径处理。"
            "kind=act 时 wait_reason 必须为 null 且 action 必须非 null；"
            "其他非 act 决策的 action 和 wait_reason 都必须为 null。"
            "click 的 target_bbox 必须是四个归一化数且 key 必须为 null；"
            "所有 click 都必须把 key 写成 JSON 字面量 null，包括点击画面中的返回按钮或返回箭头；"
            "绝不能为 click 填 return_button、back 等符号值。若目标要求返回上一页且画面中有"
            "可见的返回按钮或箭头，同时其他候选控件不可用，应 ACT 点击该返回控件。"
            "key/hotkey 的 target_bbox 必须为 null 且 key 必须是已知语义键名。"
            "expected_effect 必须描述下一帧可验证的界面或文本变化。"
            "登录、删除、支付、发送、安装标记为 critical。\n"
            "当前 OCR：\n" + ocr + required + action_target + session + policy + repair
        )

    @staticmethod
    def _parse(
        reply: str,
        snapshot: PerceptionSnapshot,
        *,
        compact: bool = False,
    ) -> PlannerOutcome:
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
        if "kind" not in payload and isinstance(payload.get("answer"), str):
            try:
                wrapped = json.loads(payload["answer"])
            except json.JSONDecodeError as exc:
                raise PlannerReplyError("grounded answer wrapper is not valid JSON") from exc
            if not isinstance(wrapped, dict):
                raise PlannerReplyError("grounded answer wrapper must contain an object")
            payload = wrapped
        try:
            kind = DecisionKind(str(payload["kind"]))
            # F11: booleans, strings, NaN/Infinity and out-of-range values are
            # rejected instead of coerced through float().
            confidence = strict_unit_interval_number(payload["confidence"])
            if compact:
                goal_status = (
                    GoalStatus.SUCCEEDED
                    if kind == DecisionKind.DONE
                    else GoalStatus.UNKNOWN
                    if kind == DecisionKind.ABSTAIN
                    else GoalStatus.IN_PROGRESS
                )
                scene_summary = "current GUI screen"
                visible_raw = []
                action_value = payload.get("action")
                label = (
                    str(action_value.get("target_label", ""))
                    if isinstance(action_value, dict)
                    else ""
                )
                explanation = f"compact decision: {kind.value} {label}".strip()
            else:
                goal_status = GoalStatus(str(payload["goal_status"]))
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
            action = GroundedVlmPlanner._parse_action(
                payload.get("action"), compact=compact
            )
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
    def _parse_action(
        value: object,
        *,
        compact: bool = False,
    ) -> GroundedAction | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise PlannerReplyError("grounded action must be an object or null")
        box_raw = value.get("target_bbox")
        box: NormalizedBox | None = None
        if box_raw is not None:
            try:
                # F11/EX07: strict numbers only (no bool, no string coercion,
                # no NaN/Inf) and ONE consistent coordinate space per frame —
                # a unit fraction next to a 0-1000 pixel value is rejected,
                # never silently rescaled.
                coordinates = strict_coordinates(box_raw)
                box = NormalizedBox(*coordinates)
            except (TypeError, ValueError, ContractViolation) as exc:
                raise PlannerReplyError(f"target_bbox is invalid: {exc}") from exc
        try:
            key_raw = value.get("key")
            return GroundedAction(
                GuiActionKind(str(value["kind"])),
                strict_bounded_text(
                    value["target_label"], max_chars=120, field="target_label"
                ),
                box,
                (
                    f"visible state changes after {value['target_label']}"
                    if compact
                    else strict_bounded_text(
                        value["expected_effect"],
                        max_chars=300,
                        field="expected_effect",
                    )
                ),
                strict_unit_interval_number(value["confidence"]),
                ActionRisk.LOW if compact else ActionRisk(str(value["risk"])),
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
            # F11: bool/str/NaN confidences never masquerade as a pass.
            confidence = strict_confidence_value(payload.get("confidence"))
            return (
                isinstance(payload, dict)
                and payload.get("approved") is True
                and confidence is not None
                and confidence >= self._threshold
                and isinstance(payload.get("reason"), str)
            )
        except (BackendUnavailableError, TypeError, ValueError, json.JSONDecodeError):
            return False
