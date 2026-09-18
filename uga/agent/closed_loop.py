from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from enum import StrEnum

from uga.agent.progress import (
    LoopDetector,
    LoopFinding,
    LoopRecord,
    ProgressTracker,
    SemanticState,
)
from uga.agent.recovery_budget import RecoveryBudget, RecoveryKind
from uga.agent.session_state import (
    ActionTrace,
    GameSessionState,
    ScreenType,
    decoy_click_blocked,
    page_anchor_signature,
    real_name_gate_active,
    stable_anchor_tokens,
)
from uga.agent.task_graph import TaskGraph, TaskStatus
from uga.capture.frame import BufferKind, Frame
from uga.control.execution_receipt import (
    ExecutionReceipt,
    aggregate_receipts,
)
from uga.control.lease import ControlMode
from uga.core.errors import ContractViolation
from uga.environment.profile import PerceptionProfile
from uga.gui.schema import GuiAction, GuiActionKind
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
from uga.policy.decision_journal import DecisionJournal, DecisionRecord
from uga.safety.action_gate import (
    generations_consistent,
    is_trusted_deterministic_source,
    point_is_clickable,
    resolved_click_point,
)
from uga.safety.sensitive_page import inspect_sensitive_page
from uga.time.clock import ClockBackend, UGATime
from uga.windows.coordinates import Point
from uga.windows.window_identity import WindowIdentity


class DecisionDisposition(StrEnum):
    EXECUTE = "execute"
    RECOVER = "recover"
    WAIT = "wait"
    REOBSERVE = "reobserve"
    TERMINATE = "terminate"
    BLOCK = "block"


class TerminalStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"


class RecoveryDirective(StrEnum):
    HIGH_RESOLUTION = "high_resolution"
    BACK = "back"


@dataclass(frozen=True, slots=True)
class SupervisedDecision:
    disposition: DecisionDisposition
    reason: str
    outcome: PlannerOutcome
    recovery: RecoveryDirective | None = None


@dataclass(frozen=True, slots=True)
class EffectObservation:
    pending: bool
    effect_observed: bool | None
    detail: str


@dataclass(slots=True)
class _PendingAction:
    action: GroundedAction
    mode: ControlMode
    visible_text: frozenset[str]
    goal_facts: tuple[tuple[str, str], ...]
    ui_state: tuple[tuple[str, bool, bool], ...]
    semantic_state: SemanticState
    target_digest: bytes
    issued_at: UGATime
    anchors: frozenset[str] = field(default_factory=frozenset)
    action_id: str | None = None
    anchor_candidate: frozenset[str] | None = None
    pixel_change_candidate_at: UGATime | None = None
    pixel_change_candidate_digest: bytes = b""
    # Execution evidence (D04): the effect clock may only start once the
    # primitives actually reached the OS, never at arbiter-acceptance time.
    submitted_action_ids: frozenset[str] = field(default_factory=frozenset)
    expected_primitives: int = 0
    receipts: list[ExecutionReceipt] = field(default_factory=list)
    executed_primitives: int = 0
    executed_at: UGATime | None = None
    completed_at: UGATime | None = None
    anchor_candidate_frame: str | None = None
    effect_candidate_frame: str | None = None
    effect_candidate_at: UGATime | None = None
    pre_effect_text: frozenset[str] = field(default_factory=frozenset)
    execution_status: str | None = None
    execution_frame_id: str | None = None
    window_identity: WindowIdentity | None = None
    geometry_generation: int = 0
    task_generation: int = 0


OutcomeVerifier = Callable[[PlannerOutcome, PerceptionSnapshot, Frame, str], bool]
KeyResolver = Callable[[str], tuple[int, ...] | None]


class GoalVerifier:
    def __init__(
        self,
        *,
        confirmation_ns: int = 500_000_000,
        threshold: float = 0.85,
        required_evidence: tuple[str, ...] = (),
    ) -> None:
        if confirmation_ns < 0 or not 0.0 <= threshold <= 1.0:
            raise ContractViolation("goal verifier configuration is invalid")
        normalized = tuple(normalize_visible_text(value) for value in required_evidence)
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ContractViolation("required goal evidence must be unique and non-empty")
        self._confirmation_ns = confirmation_ns
        self._threshold = threshold
        self._required_evidence = tuple(zip(required_evidence, normalized, strict=True))
        self._candidate: tuple[
            UGATime, str, frozenset[str], WindowIdentity, int, int
        ] | None = None
        self.last_missing_evidence: tuple[str, ...] = ()
        self.last_evidence_confidence: float | None = None

    def reset(self) -> None:
        self._candidate = None
        self.last_missing_evidence = ()
        self.last_evidence_confidence = None

    @property
    def required_evidence(self) -> tuple[str, ...]:
        return tuple(original for original, _ in self._required_evidence)

    def inspect_evidence(self, snapshot: PerceptionSnapshot) -> tuple[str, ...]:
        observed = tuple(
            (normalized, region.confidence)
            for region in snapshot.visible_text
            if (normalized := normalize_visible_text(region.text))
        )
        evidence_scores: list[float] = []
        missing: list[str] = []
        for original, required in self._required_evidence:
            confidence = _text_evidence_confidence(required, observed)
            if confidence is None:
                missing.append(original)
            else:
                evidence_scores.append(confidence)
        self.last_missing_evidence = tuple(missing)
        self.last_evidence_confidence = (
            min(evidence_scores)
            if evidence_scores and not self.last_missing_evidence
            else None
        )
        return self.last_missing_evidence

    def consider(self, outcome: PlannerOutcome, snapshot: PerceptionSnapshot) -> bool:
        if (
            outcome.kind != DecisionKind.DONE
            or outcome.goal_status != GoalStatus.SUCCEEDED
        ):
            self._candidate = None
            self.last_missing_evidence = ()
            self.last_evidence_confidence = None
            return False
        return self._consider_snapshot(
            snapshot,
            decision_confidence=outcome.confidence,
        )

    def consider_observed_evidence(self, snapshot: PerceptionSnapshot) -> bool:
        """Confirm an evidence-bound goal without trusting a contradictory action."""
        if not self._required_evidence:
            self._candidate = None
            self.inspect_evidence(snapshot)
            return False
        return self._consider_snapshot(snapshot, decision_confidence=1.0)

    def _consider_snapshot(
        self,
        snapshot: PerceptionSnapshot,
        *,
        decision_confidence: float,
    ) -> bool:
        # A nonempty screen is not a goal predicate. Completion must be
        # bound to caller-provided evidence, never to incidental OCR overlap.
        if not self._required_evidence:
            self._candidate = None
            return False
        observed_values = tuple(
            normalize_visible_text(value)
            for value in snapshot.text
            if normalize_visible_text(value)
        )
        observed = frozenset(observed_values)
        self.inspect_evidence(snapshot)
        if self.last_missing_evidence:
            self._candidate = None
            return False
        combined_confidence = min(
            decision_confidence,
            1.0
            if self.last_evidence_confidence is None
            else self.last_evidence_confidence,
        )
        if combined_confidence < self._threshold:
            self._candidate = None
            return False
        # F09: only the screen itself is goal evidence.  The model's claimed
        # visible_text is self-report, and an OCR-empty frame means the goal
        # is UNVERIFIED — two confident DONE replies confirm nothing.
        if not observed:
            self._candidate = None
            return False
        if self._candidate is None:
            self._candidate = (
                snapshot.captured_at,
                snapshot.frame_id,
                observed,
                snapshot.window_identity,
                snapshot.geometry_generation,
                snapshot.task_generation,
            )
            return False
        (
            timestamp,
            frame_id,
            previous,
            previous_window,
            previous_geometry,
            previous_task,
        ) = self._candidate
        if (
            snapshot.window_identity != previous_window
            or snapshot.geometry_generation != previous_geometry
            or snapshot.task_generation != previous_task
        ):
            # T14: completion evidence cannot be spliced across a window,
            # geometry or task boundary — restart on the new context.
            self._candidate = (
                snapshot.captured_at,
                snapshot.frame_id,
                observed,
                snapshot.window_identity,
                snapshot.geometry_generation,
                snapshot.task_generation,
            )
            return False
        elapsed = snapshot.captured_at.value_ns - timestamp.value_ns
        consistent = bool(previous & observed)
        if frame_id != snapshot.frame_id and elapsed >= self._confirmation_ns and consistent:
            self._candidate = None
            return True
        return False


def _text_evidence_confidence(
    required: str,
    observed: tuple[tuple[str, float], ...],
) -> float | None:
    """Return the strongest confidence of a literal OCR match, including split text."""
    direct = [confidence for value, confidence in observed if required in value]
    if direct:
        return max(direct)
    combined = "".join(value for value, _ in observed)
    if required not in combined:
        return None
    offsets: list[tuple[int, int, float]] = []
    cursor = 0
    for value, confidence in observed:
        offsets.append((cursor, cursor + len(value), confidence))
        cursor += len(value)
    scores: list[float] = []
    start = combined.find(required)
    while start >= 0:
        end = start + len(required)
        touched = [
            confidence
            for left, right, confidence in offsets
            if left < end and right > start
        ]
        if touched:
            scores.append(min(touched))
        start = combined.find(required, start + 1)
    return max(scores) if scores else None


class ActionValidator:
    def __init__(self, profile: PerceptionProfile) -> None:
        self._profile = profile

    def validate(
        self,
        outcome: PlannerOutcome,
        decided_snapshot: PerceptionSnapshot,
        fresh_snapshot: PerceptionSnapshot,
        decided_frame: Frame,
        fresh_frame: Frame,
        goal: str,
        *,
        secondary_verified: bool = False,
    ) -> tuple[bool, str]:
        action = outcome.action
        if action is None:
            return False, "ACT decision did not include an action"
        sensitive = inspect_sensitive_page(fresh_snapshot.visible_text, action)
        if not sensitive.requires_owner:
            sensitive = inspect_sensitive_page(decided_snapshot.visible_text, action)
        if sensitive.requires_owner:
            return False, sensitive.reason
        consistent, generation_reason = generations_consistent(
            outcome, decided_snapshot, fresh_snapshot
        )
        if not consistent:
            return False, generation_reason
        confidence = min(outcome.confidence, action.confidence)
        if confidence <= 0.85 and not secondary_verified:
            return False, "decision requires secondary verification"
        normalized_goal = normalize_visible_text(goal)
        normalized_target = normalize_visible_text(action.target_label)
        critical = any(
            normalize_visible_text(term) in normalized_target
            for term in self._profile.critical_action_terms
        )
        if action.risk == ActionRisk.CRITICAL or critical:
            explicitly_requested = bool(
                normalized_target and normalized_target in normalized_goal
            )
            if not explicitly_requested or confidence < 0.95:
                return False, "critical action is not explicitly and confidently requested"
        if action.target_box is not None:
            center = action.target_box.center
            # The gate judges the FINAL click point (offsets applied), not
            # just the box center: a nonzero pointer offset can move an
            # otherwise safe center into a forbidden strip.
            final_point = resolved_click_point(action)
            for region in self._profile.no_click_regions:
                blocked = NormalizedBox(*region)
                if blocked.contains(center) or blocked.contains(final_point):
                    return False, "target click point is inside a configured no-click region"
            if decoy_click_blocked(
                fresh_snapshot.visible_text, (final_point.x, final_point.y)
            ) or decoy_click_blocked(
                decided_snapshot.visible_text, (final_point.x, final_point.y)
            ):
                # 广告/运营诱饵横幅（首充礼包、新服冲榜、商城福利…）：无论
                # 模型给它们贴什么标签，落在诱饵文字上的点击一律拒绝。
                return False, "click target overlaps an advertising or monetization decoy"
            grounding = self._ocr_target_grounding(action, fresh_snapshot)
            grounding_decided = self._ocr_target_grounding(action, decided_snapshot)
            # A graphical control (晋升 medallion, icon buttons) carries no OCR
            # text in either frame: OCR grounding cannot judge it.  A high-
            # confidence visual-only click is allowed and bounded afterwards by
            # effect verification plus the ineffective-action escape.  A box
            # that HAD matching text in the decided frame but lost it is a
            # stale text target and stays rejected.
            visual_only = (
                grounding == "missing"
                and grounding_decided == "missing"
                and action.confidence >= _VISUAL_CLICK_MIN_CONFIDENCE
                and action.kind in {GuiActionKind.CLICK, GuiActionKind.DOUBLE_CLICK,
                                    GuiActionKind.RIGHT_CLICK, GuiActionKind.LONG_CLICK,
                                    GuiActionKind.SCROLL}
            )
            # A high-confidence icon is not exempt from visual freshness.
            if (self._target_changed(action.target_box, decided_frame, fresh_frame)
                    and grounding != "match"):
                return False, "target pixels changed while the model was deciding"
            if grounding != "match" and any(
                normalize_visible_text(region.text) == normalized_target
                and region.confidence >= 0.5 for region in fresh_snapshot.visible_text
            ):
                return False, "target text is visible elsewhere, not at the proposed location"
            if not visual_only and not secondary_verified:
                if grounding == "missing":
                    return False, "target no longer exists at the grounded OCR region"
                if grounding == "conflict":
                    return False, "OCR and model target grounding conflict"
            for element in fresh_snapshot.ui_elements:
                if (
                    action.target_box.intersection_ratio(element.box) >= 0.35
                    and normalize_visible_text(action.target_label)
                    in normalize_visible_text(element.label)
                    and not element.enabled
                ):
                    return False, "grounded UI element is disabled"
        return True, "grounded action validated on the latest frame"

    @staticmethod
    def _ocr_target_grounding(
        action: GroundedAction, snapshot: PerceptionSnapshot
    ) -> str:
        if action.target_box is None or not snapshot.visible_text:
            return "unavailable"
        target = normalize_visible_text(action.target_label)
        overlapping = tuple(
            region
            for region in snapshot.visible_text
            if max(
                action.target_box.intersection_ratio(region.box),
                region.box.intersection_ratio(action.target_box),
            )
            >= 0.35
        )
        if not overlapping:
            return "missing"
        for region in overlapping:
            text = normalize_visible_text(region.text)
            labels_match = bool(
                target
                and text
                and (
                    target in text
                    or text in target
                    or (
                        min(len(target), len(text)) >= 4
                        and SequenceMatcher(None, target, text).ratio() >= 0.72
                    )
                )
            )
            if labels_match and region.confidence >= 0.5:
                return "match"
        return "conflict"

    @staticmethod
    def _target_changed(box: NormalizedBox, decided: Frame, fresh: Frame) -> bool:
        if (
            decided.window_identity != fresh.window_identity
            or decided.width != fresh.width
            or decided.height != fresh.height
            or decided.client_rect != fresh.client_rect
        ):
            return True
        before = region_digest(decided, box)
        after = region_digest(fresh, box)
        if not before or len(before) != len(after):
            return True
        difference = sum(a != b for a, b in zip(before, after, strict=True)) / len(before)
        return difference > 0.25


def region_digest(frame: Frame, box: NormalizedBox) -> bytes:
    if frame.buffer_handle.kind != BufferKind.CPU_BYTES:
        raise ContractViolation("target verification requires a CPU-addressable frame")
    payload = frame.buffer_handle.readonly_view()
    left = max(0, int(box.left * frame.width))
    right = min(frame.width, max(left + 1, int(box.right * frame.width)))
    top = max(0, int(box.top * frame.height))
    bottom = min(frame.height, max(top + 1, int(box.bottom * frame.height)))
    # F07: the old [::16] byte stride sampled one colour channel of every
    # fourth pixel, so a pure red or green state change stayed invisible to
    # every freshness and effect comparison.  Two bytes per sampled pixel —
    # luminance plus a channel XOR — always cover every colour channel while
    # ignoring alpha and stride padding, and the deterministic spatial grid
    # keeps the sampling bounded on huge regions.
    width = right - left
    step = max(1, math.isqrt(max(1, width * (bottom - top)) // 4096))
    stride = frame.stride_bytes
    digest = bytearray()
    for row in range(top, bottom, step):
        base = row * stride + left * 4
        for offset in range(0, width * 4, step * 4):
            blue = payload[base + offset]
            green = payload[base + offset + 1]
            red = payload[base + offset + 2]
            digest.append((29 * blue + 150 * green + 77 * red) >> 8)
            digest.append(blue ^ green ^ red)
    return bytes(digest)


class ClosedLoopSupervisor:
    """Stateful verifier between a VLM proposal and physical GUI input."""

    def __init__(
        self,
        clock: ClockBackend,
        profile: PerceptionProfile,
        *,
        verifier: OutcomeVerifier | None = None,
        max_recoveries: int = 2,
        recovery_budget: RecoveryBudget | None = None,
        task_graph: TaskGraph | None = None,
        task_node_id: str | None = None,
        goal_evidence: tuple[str, ...] = (),
        goal_action_target: str | None = None,
        back_hotspot: tuple[float, float] | None = None,
        close_hotspot: tuple[float, float] | None = None,
        promote_hotspot: tuple[float, float] | None = None,
        available_keys: frozenset[str] | None = None,
        session: GameSessionState | None = None,
        journal: DecisionJournal | None = None,
        max_failed_back_recoveries: int = 2,
        max_stuck_waits: int = 10,
        on_exit_executed: Callable[[], None] | None = None,
        max_execution_frame_age_ns: int = 1_000_000_000,
        allow_calibrated_intents: bool = True,
        verify_all_actions: bool = False,
    ) -> None:
        if not 0 <= max_recoveries <= 2:
            raise ContractViolation("closed-loop recoveries must be within [0, 2]")
        if (task_graph is None) != (task_node_id is None):
            raise ContractViolation("task graph and active node must be configured together")
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
        if available_keys is not None and (
            not isinstance(available_keys, frozenset)
            or any(not isinstance(key, str) or not key.strip() for key in available_keys)
        ):
            raise ContractViolation("available keys must be a frozenset of key names")
        if not 1 <= max_failed_back_recoveries <= 8:
            raise ContractViolation("failed back recovery bound must be within [1, 8]")
        if not 2 <= max_stuck_waits <= 100:
            raise ContractViolation("stuck-wait bound must be within [2, 100]")
        if type(max_execution_frame_age_ns) is not int or max_execution_frame_age_ns <= 0:
            raise ContractViolation("execution frame age budget must be a positive integer")
        self._max_execution_frame_age_ns = max_execution_frame_age_ns
        self._clock = clock
        self._profile = profile
        self._verifier = verifier
        self._allow_calibrated_intents = allow_calibrated_intents
        self._verify_all_actions = verify_all_actions
        if back_hotspot is None:
            self._back_hotspot: tuple[float, float] | None = None
        else:
            hotspot_x, hotspot_y = back_hotspot
            self._back_hotspot = (float(hotspot_x), float(hotspot_y))
        self._back_box = None if self._back_hotspot is None else _hotspot_box(self._back_hotspot)
        if close_hotspot is None:
            self._close_hotspot: tuple[float, float] | None = None
        else:
            close_x, close_y = close_hotspot
            self._close_hotspot = (float(close_x), float(close_y))
        self._close_box = (
            None if self._close_hotspot is None else _hotspot_box(self._close_hotspot)
        )
        if promote_hotspot is None:
            self._promote_hotspot: tuple[float, float] | None = None
        else:
            promote_x, promote_y = promote_hotspot
            self._promote_hotspot = (float(promote_x), float(promote_y))
        self._promote_box = (
            None
            if self._promote_hotspot is None
            else _hotspot_box(self._promote_hotspot)
        )
        self._available_keys = available_keys
        self._session = session
        self._journal = journal
        self._max_failed_back_recoveries = max_failed_back_recoveries
        self._max_stuck_waits = max_stuck_waits
        if on_exit_executed is not None and not callable(on_exit_executed):
            raise ContractViolation("on_exit_executed must be callable")
        self._on_exit_executed = on_exit_executed
        self._escaped_action_keys: dict[str, float] = {}
        self._back_recovery_streak = 0
        self._goal = GoalVerifier(
            confirmation_ns=profile.page_stable_ms * 1_000_000,
            threshold=0.85,
            required_evidence=goal_evidence,
        )
        self._goal_action_target = (
            None if goal_action_target is None else normalize_visible_text(goal_action_target)
        )
        if goal_action_target is not None and not self._goal_action_target:
            raise ContractViolation("goal action target cannot be blank")
        self._preferred_action_consumed = False
        self._consumed_target_rejections = 0
        self._validator = ActionValidator(profile)
        self._feedback: deque[dict[str, object]] = deque(maxlen=6)
        self._pending: _PendingAction | None = None
        self._uncertain_retries = 0
        self._ineffective: dict[str, int] = {}
        self._logical_actions_issued = 0
        self._verified_effect_actions = 0
        self._ineffective_actions = 0
        self._not_executed_actions = 0
        self._consecutive_not_executed = 0
        self._stale_results_discarded = 0
        self._last_ineffective_key: str | None = None
        self._consecutive_same_ineffective = 0
        self._max_consecutive_same_ineffective = 0
        self._last_started_action_semantic_key: str | None = None
        self._consecutive_same_started_action = 0
        self._no_safe_state_signature: str | None = None
        self._no_safe_state_repeats = 0
        self._wait_state_signature: str | None = None
        self._wait_state_repeats = 0
        self._progress = ProgressTracker()
        self._loops = LoopDetector()
        self._max_recoveries = max_recoveries
        # F05/F06: the run-level budget is owned by the composition root and
        # survives supervisor rebuilds; without one, a local budget enforces
        # the same max_recoveries semantics (0 disables every recovery).
        self._recovery_budget = recovery_budget or RecoveryBudget(max_recoveries)
        self._recovery_count = 0
        self._pending_recovery: RecoveryDirective | None = None
        self._recovery_in_progress: RecoveryDirective | None = None
        self._last_failed_action_key: str | None = None
        self._task_graph = task_graph
        self._task_node_id = task_node_id
        if task_graph is not None and task_node_id is not None:
            try:
                node = task_graph.get(task_node_id)
            except KeyError as exc:
                raise ContractViolation("closed-loop task node does not exist") from exc
            if node.status == TaskStatus.READY:
                task_graph.activate(task_node_id)
            elif node.status != TaskStatus.ACTIVE:
                raise ContractViolation("closed-loop task node must be ready or active")
        self.last_loop_finding: LoopFinding | None = None
        self.status = TerminalStatus.RUNNING
        self.termination_reason: str | None = None
        self.goal_confidence: float | None = None
        self.last_effect_observed: bool | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status != TerminalStatus.RUNNING

    @property
    def session(self) -> GameSessionState | None:
        return self._session

    @property
    def back_hotspot(self) -> tuple[float, float] | None:
        if self._back_hotspot is None:
            return None
        x, y = self._back_hotspot
        return (x, y)

    @property
    def recovery_count(self) -> int:
        return self._recovery_count

    @property
    def high_resolution_retry(self) -> bool:
        return (
            self._pending_recovery == RecoveryDirective.HIGH_RESOLUTION
            or self._uncertain_retries == 1
        )

    @property
    def preferred_action_available(self) -> bool:
        return not self._preferred_action_consumed

    @property
    def no_click_regions(self) -> tuple[tuple[float, float, float, float], ...]:
        return self._profile.no_click_regions

    def diagnostics(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "termination_reason": self.termination_reason,
            "goal_confidence": self.goal_confidence,
            "required_goal_evidence": list(self._goal.required_evidence),
            "missing_goal_evidence": list(self._goal.last_missing_evidence),
            "goal_evidence_confidence": self._goal.last_evidence_confidence,
            "goal_action_target": self._goal_action_target,
            "preferred_action_consumed": self._preferred_action_consumed,
            "consumed_target_rejections": self._consumed_target_rejections,
            "recovery_count": self._recovery_count,
            "back_recovery_streak": self._back_recovery_streak,
            "back_hotspot": self._back_hotspot,
            "stuck_wait_repeats": self._wait_state_repeats,
            "session": None if self._session is None else self._session.to_envelope(),
            "persistence_error": (
                None
                if self._session is None
                else self._session.last_persistence_error
            ),
            "last_effect_observed": self.last_effect_observed,
            "logical_actions_issued": self._logical_actions_issued,
            "verified_effect_actions": self._verified_effect_actions,
            "ineffective_actions": self._ineffective_actions,
            "not_executed_actions": self._not_executed_actions,
            "consecutive_not_executed_actions": self._consecutive_not_executed,
            "recovery_budget_consumed": self._recovery_budget.consumed,
            "recovery_budget_limit": self._recovery_budget.limit,
            "recovery_budget_exhausted": self._recovery_budget.exhausted,
            "pending_action": self._pending is not None,
            "stale_results_discarded": self._stale_results_discarded,
            "max_consecutive_same_ineffective_action": (
                self._max_consecutive_same_ineffective
            ),
            "consecutive_same_started_action": self._consecutive_same_started_action,
            "no_safe_state_repeats": self._no_safe_state_repeats,
            "task_status": (
                None
                if self._task_graph is None or self._task_node_id is None
                else self._task_graph.get(self._task_node_id).status.value
            ),
            "last_loop_finding": (
                None
                if self.last_loop_finding is None
                else {
                    "kind": self.last_loop_finding.kind.value,
                    "cycle_length": self.last_loop_finding.cycle_length,
                    "detail": self.last_loop_finding.detail,
                }
            ),
            "state_action_ring": [
                {
                    "state_signature": item.state_signature,
                    "action_key": item.action_key,
                    "effect_observed": item.effect_observed,
                    "next_state_signature": item.next_state_signature,
                    "semantic_progress": item.semantic_progress,
                }
                for item in self._loops.records
            ],
        }

    def observe(self, snapshot: PerceptionSnapshot, frame: Frame) -> EffectObservation:
        sensitive = inspect_sensitive_page(snapshot.visible_text)
        if sensitive.requires_owner:
            self._goal.reset()
            self._pending = None
            self.last_effect_observed = None
            return EffectObservation(False, None, sensitive.reason)
        if self._session is not None:
            self._session.observe_snapshot(snapshot, snapshot.captured_at.value_ns)
        pending = self._pending
        if pending is None:
            return EffectObservation(False, None, "no action awaiting verification")
        live_task = (
            snapshot.task_generation if self._session is None else self._session.task_generation
        )
        if (
            snapshot.window_identity != pending.window_identity
            or snapshot.geometry_generation != pending.geometry_generation
            or live_task != pending.task_generation
        ):
            self._pending = None
            self.last_effect_observed = None
            detail = "effect context changed; no causal effect claim"
            self._finish_trace(pending, "invalidated", detail)
            self._journal_effect(pending, "invalidated", detail)
            return EffectObservation(False, None, detail)
        observed_now_ns = max(snapshot.captured_at.value_ns, self._clock.now().value_ns)
        minimum_ns = 250_000_000
        timeout_ns = self._profile.action_effect_timeout_ms * 1_000_000
        # D04: queued/accepted never substitutes for executed.  A click whose
        # primitives only partly reached the OS is PARTIAL, never a
        # completed action; until every expected primitive carries a terminal
        # EXECUTED receipt there is no effect clock to run, and an action
        # still unproven at the deadline completes as not_executed — never as
        # a verified success.
        if (
            pending.expected_primitives
            and pending.executed_primitives < pending.expected_primitives
        ):
            all_terminal = len(pending.receipts) >= pending.expected_primitives
            if not all_terminal:
                unproven_elapsed = (
                    observed_now_ns - pending.issued_at.value_ns
                )
                if unproven_elapsed < timeout_ns:
                    return EffectObservation(
                        True, None, "waiting for terminal execution receipts"
                    )
            status = pending.execution_status or (
                "partial" if pending.executed_primitives else "unknown"
            )
            self._pending = None
            self.last_effect_observed = False
            self._not_executed_actions += 1
            self._consecutive_not_executed += 1
            detail = f"execution incomplete ({status}); no effect claim"
            self._finish_trace(pending, "not_executed", detail)
            self._journal_effect(pending, "not_executed", detail)
            if self._consecutive_not_executed >= 3:
                # The input gateway keeps refusing (focus lost, window gone,
                # integrity mismatch): re-proposing forever would only burn
                # model calls, so stand down honestly instead of spinning.
                self._stop_blocked(
                    "the input executor refused "
                    f"{self._consecutive_not_executed} actions in a row"
                )
            return EffectObservation(False, False, "action execution was never confirmed")
        elapsed_ns = observed_now_ns - (
            pending.completed_at.value_ns
            if pending.completed_at is not None
            else pending.issued_at.value_ns
        )
        # Numeric-only OCR jitter (counters, timers, ratios) must never fake an
        # effect, so the change comparison runs on digit-stripped anchor tokens.
        semantic_text = stable_anchor_tokens(snapshot.text)
        ui_state = tuple(
            (normalize_visible_text(element.label), element.enabled, element.selected)
            for element in snapshot.ui_elements
        )
        semantic_state = self._progress.state(snapshot)
        has_new_evidence = (
            snapshot.frame_id != pending.execution_frame_id
            and snapshot.captured_at.value_ns > (
                pending.completed_at.value_ns if pending.completed_at is not None
                else pending.issued_at.value_ns
            )
        )
        semantic_text_changed = _meaningful_text_change(
            pending.visible_text, semantic_text
        )
        semantic_changed = has_new_evidence and (
            snapshot.mode != pending.mode
            or semantic_text_changed
            or snapshot.goal_facts != pending.goal_facts
            or ui_state != pending.ui_state
        )
        target_changed = False
        current_digest = b""
        if has_new_evidence and pending.action.target_box is not None:
            current_digest = region_digest(frame, pending.action.target_box)
            target_changed = _digest_difference(pending.target_digest, current_digest) > 0.1
        if elapsed_ns < minimum_ns:
            return EffectObservation(True, None, "waiting for the minimum action effect window")
        # F04: stabilization candidates may refresh their own windows, but
        # the absolute effect deadline is never extended — every waiting branch
        # below must let the flow fall through to the timeout resolution once
        # the deadline has passed.
        deadline_expired = elapsed_ns >= max(minimum_ns, timeout_ns)
        # Page-header anchors (灵宠/召唤/布阵/主线 …) are the most stable page
        # transition signal; confirm a flip across two observations so a single
        # OCR miss cannot fake a page change.
        current_anchors = page_anchor_signature(snapshot.visible_text)
        anchor_flip = has_new_evidence and current_anchors != pending.anchors
        anchor_confirmed = False
        if anchor_flip and not semantic_changed and not target_changed:
            if pending.anchor_candidate != current_anchors:
                if not deadline_expired:
                    pending.anchor_candidate = current_anchors
                    pending.anchor_candidate_frame = snapshot.frame_id
                    return EffectObservation(
                        True, None, "waiting for the page anchor change to stabilize"
                    )
                # At the deadline an unconfirmed single-frame flip resolves
                # honestly below instead of waiting for a confirming frame.
            else:
                anchor_confirmed = snapshot.frame_id != pending.anchor_candidate_frame
        elif not anchor_flip:
            pending.anchor_candidate = None
        persistent_target_change = False
        target_still_grounded = (
            ActionValidator._ocr_target_grounding(pending.action, snapshot) == "match"
        )
        if target_changed and not semantic_changed and not target_still_grounded:
            if pending.pixel_change_candidate_at is None:
                if not deadline_expired:
                    pending.pixel_change_candidate_at = snapshot.captured_at
                    pending.pixel_change_candidate_digest = current_digest
                    return EffectObservation(
                        True, None, "waiting for target pixel change to stabilize"
                    )
            else:
                stable_ns = (
                    snapshot.captured_at.value_ns
                    - pending.pixel_change_candidate_at.value_ns
                )
                candidate_stable = (
                    _digest_difference(
                        pending.pixel_change_candidate_digest, current_digest
                    )
                    <= 0.02
                )
                if not candidate_stable:
                    if not deadline_expired:
                        # Animated pages may refresh the stabilization window,
                        # but never the absolute deadline: permanent animation
                        # falls through to the timeout resolution below.
                        pending.pixel_change_candidate_at = snapshot.captured_at
                        pending.pixel_change_candidate_digest = current_digest
                        return EffectObservation(
                            True, None, "transient target pixels are still changing"
                        )
                else:
                    # A stable candidate is real evidence of change; the
                    # persistence window cannot be extended past the deadline,
                    # so an already-stable change resolves as persistent.
                    persistent_target_change = (
                        stable_ns >= minimum_ns
                    )
                    if not persistent_target_change and not deadline_expired:
                        return EffectObservation(
                            True, None, "waiting for target pixel change to persist"
                        )
        elif not target_changed or target_still_grounded:
            pending.pixel_change_candidate_at = None
            pending.pixel_change_candidate_digest = b""
        changed = semantic_changed or persistent_target_change or anchor_confirmed
        effect_detail = "observed GUI transition; goal completion is independently verified"
        if pending.action.effect is not None:
            condition, effect_detail = self._specified_effect(
                pending, snapshot, frame, has_new_evidence,
                persistent_target_change, ui_state,
            )
            if not condition:
                pending.effect_candidate_frame = None
                pending.effect_candidate_at = None
                changed = False
            elif pending.effect_candidate_frame is None:
                pending.effect_candidate_frame = snapshot.frame_id
                pending.effect_candidate_at = snapshot.captured_at
                changed = False
            else:
                assert pending.effect_candidate_at is not None
                changed = (snapshot.frame_id != pending.effect_candidate_frame
                           and snapshot.captured_at.value_ns
                           - pending.effect_candidate_at.value_ns >= 100_000_000)
        elif (ActionValidator._ocr_target_grounding(pending.action, snapshot) == "match"
              and normalize_visible_text(pending.action.target_label) in pending.pre_effect_text
              and snapshot.mode == pending.mode and snapshot.goal_facts == pending.goal_facts
              and ui_state == pending.ui_state):
            # An unchanged target plus an unrelated notification is not its effect.
            changed = False
        if changed:
            self._pending = None
            self.last_effect_observed = True
            self._verified_effect_actions += 1
            self._consecutive_not_executed = 0
            self._last_ineffective_key = None
            self._consecutive_same_ineffective = 0
            self._reset_no_safe_waits()
            self._uncertain_retries = 0
            # A verified page transition proves the back control works again.
            self._back_recovery_streak = 0
            if self._matches_goal_action_target(pending.action):
                self._preferred_action_consumed = True
            finding = self._record_action_result(
                pending,
                semantic_state,
                effect_observed=True,
                target_changed=persistent_target_change,
            )
            if finding is not None:
                self._last_failed_action_key = self._action_key(pending.action)
                self._request_recovery(finding.detail)
            else:
                self._recovery_in_progress = None
                self._last_failed_action_key = None
            self._ineffective.pop(self._action_key(pending.action), None)
            self._finish_trace(pending, "verified", effect_detail)
            self._journal_effect(pending, "verified", effect_detail)
            return EffectObservation(False, True, effect_detail)
        if elapsed_ns < max(minimum_ns, timeout_ns):
            return EffectObservation(True, None, "waiting for the expected visual effect")
        key = self._action_key(pending.action)
        self._ineffective[key] = self._ineffective.get(key, 0) + 1
        self._ineffective_actions += 1
        if key == self._last_ineffective_key:
            self._consecutive_same_ineffective += 1
        else:
            self._last_ineffective_key = key
            self._consecutive_same_ineffective = 1
        self._max_consecutive_same_ineffective = max(
            self._max_consecutive_same_ineffective,
            self._consecutive_same_ineffective,
        )
        self._pending = None
        self.last_effect_observed = False
        self._consecutive_not_executed = 0
        finding = self._record_action_result(
            pending,
            semantic_state,
            effect_observed=False,
            target_changed=False,
        )
        self._last_failed_action_key = key
        self._finish_trace(pending, "ineffective", "no verified visual effect")
        self._journal_effect(pending, "ineffective", "no verified visual effect")
        is_exit_attempt = pending.action.target_label in {"ui_back", "ui_close"}
        if is_exit_attempt or self._recovery_in_progress == RecoveryDirective.BACK:
            # A visual exit (top-left ribbon or top-right X) that changed
            # nothing alternates to the other control from the freshest frame;
            # after the bound is exceeded the failure is reported instead of
            # cycling forever.
            self._recovery_in_progress = None
            self._back_recovery_streak += 1
            if self._back_recovery_streak >= self._max_failed_back_recoveries:
                self._stop_blocked(
                    "visual exit controls produced no verified page change in "
                    f"{self._back_recovery_streak} consecutive attempts "
                    "(top-left ribbon and top-right X both tried)"
                )
        elif self._ineffective[key] >= 2 or finding is not None:
            self._request_recovery(
                finding.detail if finding is not None else "same action failed twice"
            )
        return EffectObservation(False, False, "action produced no verified visual effect")

    def assess(
        self,
        outcome: PlannerOutcome,
        decided_snapshot: PerceptionSnapshot,
        fresh_snapshot: PerceptionSnapshot,
        decided_frame: Frame,
        fresh_frame: Frame,
        goal: str,
        *,
        decision_source: str | None = None,
    ) -> SupervisedDecision:
        if self.is_terminal:
            return SupervisedDecision(
                DecisionDisposition.BLOCK,
                self.termination_reason or "loop already stopped",
                outcome,
            )
        if real_name_gate_active(fresh_snapshot.visible_text):
            # 实名登记/防沉迷是账号级法定门禁（个人身份信息）：代理绝不代
            # 填，也不在这类表单上执行任何动作——挂起全部决策，等 owner
            # 完成登记后表单消失、自动恢复推进。
            return SupervisedDecision(
                DecisionDisposition.WAIT,
                "real-name registration gate: standing by for the owner",
                outcome,
            )
        consistent, _ = generations_consistent(outcome, decided_snapshot, fresh_snapshot)
        if not consistent:
            self._stale_results_discarded += 1
            return SupervisedDecision(
                DecisionDisposition.REOBSERVE, "decision generation became stale", outcome
            )
        sensitive = inspect_sensitive_page(fresh_snapshot.visible_text, outcome.action)
        if not sensitive.requires_owner:
            sensitive = inspect_sensitive_page(decided_snapshot.visible_text, outcome.action)
        if sensitive.requires_owner:
            self._goal.reset()
            # Source tags and goals cannot authorize sensitive pages.
            # WAIT here never escalates to an automatic recovery click.
            return SupervisedDecision(DecisionDisposition.WAIT, sensitive.reason, outcome)
        recovery = self._pending_recovery
        recovering_high_resolution = recovery == RecoveryDirective.HIGH_RESOLUTION
        if recovering_high_resolution:
            self._pending_recovery = None
        if outcome.kind != DecisionKind.WAIT:
            self._reset_no_safe_waits()
        elif outcome.wait_reason != WaitReason.NO_SAFE_ACTION:
            # A non-no-safe wait breaks the no-safe streak but must keep the
            # generic identical-state waiting streak alive.
            self._no_safe_state_signature = None
            self._no_safe_state_repeats = 0
        if outcome.kind == DecisionKind.DONE:
            if not self._goal.required_evidence:
                return self._block(
                    outcome, "goal verification is unconfigured; provide explicit goal evidence"
                )
            if self._goal.consider(outcome, fresh_snapshot):
                evidence_confidence = self._goal.last_evidence_confidence
                self.status = TerminalStatus.SUCCEEDED
                self.termination_reason = "goal_confirmed"
                self.goal_confidence = min(
                    outcome.confidence,
                    1.0 if evidence_confidence is None else evidence_confidence,
                )
                self._complete_task(success=True)
                return SupervisedDecision(
                    DecisionDisposition.TERMINATE, "goal confirmed on two fresh frames", outcome
                )
            if self._goal.last_missing_evidence:
                missing = ", ".join(self._goal.last_missing_evidence)
                return SupervisedDecision(
                    DecisionDisposition.REOBSERVE,
                    f"goal completion rejected; missing fresh OCR evidence: {missing}",
                    outcome,
                )
            evidence_confidence = self._goal.last_evidence_confidence
            if min(
                outcome.confidence,
                1.0 if evidence_confidence is None else evidence_confidence,
            ) < 0.85:
                return self._retry_or_block(
                    outcome,
                    "goal completion confidence is below 0.85",
                )
            return SupervisedDecision(
                DecisionDisposition.REOBSERVE,
                "goal completion awaits a second fresh frame",
                outcome,
            )
        if self._goal.required_evidence:
            evidence_confirmed = self._goal.consider_observed_evidence(fresh_snapshot)
            if not self._goal.last_missing_evidence:
                evidence_confidence = self._goal.last_evidence_confidence or 0.0
                if evidence_confidence < 0.85:
                    return self._retry_or_block(
                        outcome,
                        "required completion evidence is visible but its OCR "
                        "confidence is below 0.85",
                    )
                if evidence_confirmed:
                    self.status = TerminalStatus.SUCCEEDED
                    self.termination_reason = "goal_confirmed"
                    self.goal_confidence = evidence_confidence
                    self._complete_task(success=True)
                    return SupervisedDecision(
                        DecisionDisposition.TERMINATE,
                        "goal confirmed from required OCR evidence on two fresh frames; "
                        "contradictory planner action suppressed",
                        outcome,
                    )
                return SupervisedDecision(
                    DecisionDisposition.REOBSERVE,
                    "required completion evidence is visible; suppressing physical "
                    "action while awaiting a second fresh frame",
                    outcome,
                )
        if outcome.kind == DecisionKind.WAIT:
            self._goal.consider(outcome, fresh_snapshot)
            state = self._stable_state_key(fresh_snapshot)
            if state == self._wait_state_signature:
                self._wait_state_repeats += 1
            else:
                self._wait_state_signature = state
                self._wait_state_repeats = 1
            if outcome.wait_reason == WaitReason.NO_SAFE_ACTION:
                # Two consecutive no-safe decisions are the signal itself:
                # OCR jitter used to reset the state-signature match here,
                # which let animated pages wait forever.
                self._no_safe_state_repeats += 1
                if recovering_high_resolution:
                    return self._advance_loop_recovery(
                        outcome,
                        "high-resolution recovery still found no safe action",
                    )
                if self._no_safe_state_repeats >= 2:
                    self._request_recovery(
                        "same state returned no safe action twice"
                    )
                    if self.is_terminal:
                        return SupervisedDecision(
                            DecisionDisposition.BLOCK,
                            self.termination_reason or "no safe recovery remains",
                            outcome,
                        )
                    return SupervisedDecision(
                        DecisionDisposition.REOBSERVE,
                        "re-observing repeated no-safe-action state at high resolution",
                        outcome,
                    )
            elif recovering_high_resolution and self._wait_state_repeats >= 2:
                return self._advance_loop_recovery(
                    outcome,
                    "high-resolution recovery did not leave the repeatedly waited page",
                )
            elif (
                self._wait_state_repeats >= self._stuck_wait_bound(fresh_snapshot)
            ):
                # The handoff contract: identical-state waiting must never run
                # forever.  Loading/animation waits get a generous bound (the
                # loader usually resolves); feature pages give up sooner —
                # one-shot quest steps (洗髓 0/1 style) finish with a single
                # click and the page must be left afterwards.
                self._request_recovery(
                    f"planner waited {self._wait_state_repeats} times on the same state"
                )
                if self.is_terminal:
                    return SupervisedDecision(
                        DecisionDisposition.BLOCK,
                        self.termination_reason or "no safe recovery remains",
                        outcome,
                    )
                return SupervisedDecision(
                    DecisionDisposition.REOBSERVE,
                    "re-observing long-waited state at high resolution",
                    outcome,
                )
            return SupervisedDecision(
                DecisionDisposition.WAIT,
                f"planner wait: {outcome.wait_reason.value}",  # type: ignore[union-attr]
                outcome,
            )
        if recovery == RecoveryDirective.BACK:
            if (
                outcome.kind == DecisionKind.ACT
                and outcome.action is not None
                and self._action_key(outcome.action) != self._last_failed_action_key
                and not self._action_in_recent_failures(outcome.action)
            ):
                # The exit was armed because the model kept waiting, but it has
                # now proposed a concrete action that is not part of a recent
                # failure pattern: a real proposal beats leaving the page.
                # Cancel the exit and validate the proposal normally.
                self._pending_recovery = None
                self._reset_no_safe_waits()
            else:
                self._pending_recovery = None
                self._recovery_in_progress = RecoveryDirective.BACK
                return SupervisedDecision(
                    DecisionDisposition.RECOVER,
                    "executing configured safe back recovery",
                    outcome,
                    RecoveryDirective.BACK,
                )
        if outcome.kind in {DecisionKind.ABSTAIN, DecisionKind.RECOVER}:
            if recovering_high_resolution:
                return self._advance_loop_recovery(
                    outcome, "high-resolution recovery did not find a safe action"
                )
            return self._retry_or_block(outcome, "planner did not identify a safe action")
        assert outcome.action is not None
        if (
            self._available_keys is not None
            and outcome.action.kind in {GuiActionKind.KEY, GuiActionKind.HOTKEY}
            and outcome.action.key not in self._available_keys
        ):
            # An unbound semantic key must never reach the executor (it would
            # crash the submission contract); treat it like any other unsafe
            # proposal and let the bounded retry path handle repetition.
            return self._retry_or_block(
                outcome,
                f"grounded key has no confirmed binding: {outcome.action.key}",
            )
        if not recovering_high_resolution and self._should_escape_repeated_action(
            outcome.action
        ):
            # Repeated-action escape is a recovery, not a budget-free
            # navigation shortcut. The common path consumes the shared budget.
            self._last_started_action_semantic_key = None
            self._consecutive_same_started_action = 0
            return self._advance_loop_recovery(
                outcome,
                "same non-progress control was executed twice; leaving the panel to re-observe",
            )
        if (
            self._goal_action_target is not None
            and not self._matches_goal_action_target(outcome.action)
        ):
            return self._retry_or_block(
                outcome,
                "single-step action does not match the configured navigation target",
            )
        if self._preferred_action_consumed and self._matches_goal_action_target(outcome.action):
            self._consumed_target_rejections += 1
            if self._consumed_target_rejections >= 2:
                return self._block(
                    outcome,
                    "single-step navigation target already produced an effect; "
                    "completion evidence is still absent",
                )
            return SupervisedDecision(
                DecisionDisposition.REOBSERVE,
                "single-step navigation target already produced an effect; refusing repeat",
                outcome,
            )
        if (
            outcome.action.target_label in {"ui_back", "ui_close", "ui_promote"}
            and outcome.action.target_box is not None
            and (
                self._back_hotspot is not None
                or self._close_hotspot is not None
                or self._promote_hotspot is not None
            )
            and is_trusted_deterministic_source(decision_source)
            and not self._verify_all_actions
        ):
            # A reserved label is display text, not a permission: it only
            # reaches this deterministic branch when trusted runtime rule code
            # assigned the outcome (an ocr_* fast path).  Even then the shared
            # generation guard and the final-point gate apply — a recreated
            # window or a forbidden landing point still rejects the click.
            consistent, generation_reason = generations_consistent(
                outcome, decided_snapshot, fresh_snapshot
            )
            if not consistent:
                self._stale_results_discarded += 1
                return SupervisedDecision(
                    DecisionDisposition.REOBSERVE,
                    "stale decision discarded; observing the current generation",
                    outcome,
                )
            verdict = point_is_clickable(
                resolved_click_point(outcome.action),
                no_click_regions=self._profile.no_click_regions,
                decided_text=decided_snapshot.visible_text,
                fresh_text=fresh_snapshot.visible_text,
            )
            if not verdict.allowed:
                return self._retry_or_block(outcome, verdict.reason)
            self._uncertain_retries = 0
            return SupervisedDecision(
                DecisionDisposition.EXECUTE,
                "deterministic exit control executed without OCR grounding",
                outcome,
            )
        if (self._allow_calibrated_intents and not self._verify_all_actions
                and self._is_back_intent(outcome.action)):
            # Graphical back/close controls carry no OCR text, so model boxes
            # on them can never pass OCR grounding and would block-loop the
            # cycle.  Route the model's exit *intent* through the user-
            # confirmed calibrated hotspots instead (alternating as attempts
            # fail; same trust level as recovery).  The model's own box is
            # never executed, and the shared generation and final-point gates
            # still apply to the routed click.
            consistent, generation_reason = generations_consistent(
                outcome, decided_snapshot, fresh_snapshot
            )
            if not consistent:
                self._stale_results_discarded += 1
                return SupervisedDecision(
                    DecisionDisposition.REOBSERVE,
                    "stale decision discarded; observing the current generation",
                    outcome,
                )
            label, hotspot, box = self._next_exit()
            assert box is not None and hotspot is not None
            verdict = point_is_clickable(
                Point(hotspot[0], hotspot[1]),
                no_click_regions=self._profile.no_click_regions,
                decided_text=decided_snapshot.visible_text,
                fresh_text=fresh_snapshot.visible_text,
            )
            if not verdict.allowed:
                return self._retry_or_block(outcome, verdict.reason)
            outcome = replace(
                outcome,
                action=GroundedAction(
                    GuiActionKind.CLICK,
                    label,
                    box,
                    "leave the current page through the calibrated exit control",
                    outcome.action.confidence,
                ),
            )
            assert outcome.action is not None
            self._uncertain_retries = 0
            return SupervisedDecision(
                DecisionDisposition.EXECUTE,
                f"model exit intent routed to the calibrated {label} control",
                outcome,
            )
        assert outcome.action is not None
        key = self._action_key(outcome.action)
        if recovering_high_resolution and key == self._last_failed_action_key:
            return self._advance_loop_recovery(
                outcome, "high-resolution recovery repeated the failed action"
            )
        confidence = min(outcome.confidence, outcome.action.confidence)
        if confidence <= 0.55:
            if recovering_high_resolution:
                return self._advance_loop_recovery(
                    outcome, "high-resolution recovery remained below confidence threshold"
                )
            return self._retry_or_block(outcome, "decision confidence is at or below 0.55")
        requires_verifier = self._verify_all_actions or confidence <= 0.85
        secondary_verified = False
        if requires_verifier:
            if self._verifier is None:
                if recovering_high_resolution:
                    return self._advance_loop_recovery(
                        outcome, "high-resolution recovery still requires a verifier"
                    )
                return self._retry_or_block(outcome, "secondary verifier is unavailable")
            if not self._verifier(outcome, fresh_snapshot, fresh_frame, goal):
                if recovering_high_resolution:
                    return self._advance_loop_recovery(
                        outcome, "secondary verifier rejected high-resolution recovery"
                    )
                return self._retry_or_block(outcome, "secondary verifier rejected the action")
            secondary_verified = True
        valid, reason = self._validator.validate(
            outcome,
            decided_snapshot,
            fresh_snapshot,
            decided_frame,
            fresh_frame,
            goal,
            secondary_verified=secondary_verified,
        )
        if not valid:
            if reason in {
                "decision generation became stale",
                "target no longer exists at the grounded OCR region",
                "target text is visible elsewhere, not at the proposed location",
                "target pixels changed while the model was deciding",
            }:
                self._stale_results_discarded += 1
                return SupervisedDecision(
                    DecisionDisposition.REOBSERVE,
                    "stale decision discarded; observing the current generation",
                    outcome,
                )
            if (
                "OCR and model" in reason
                and not secondary_verified
                and self._verifier is not None
                and self._verifier(outcome, fresh_snapshot, fresh_frame, goal)
            ):
                valid, reason = self._validator.validate(
                    outcome,
                    decided_snapshot,
                    fresh_snapshot,
                    decided_frame,
                    fresh_frame,
                    goal,
                    secondary_verified=True,
                )
                if valid:
                    self._uncertain_retries = 0
                    if recovering_high_resolution:
                        self._recovery_in_progress = RecoveryDirective.HIGH_RESOLUTION
                    return SupervisedDecision(
                        DecisionDisposition.EXECUTE,
                        "secondary verifier resolved OCR conflict",
                        outcome,
                    )
            if recovering_high_resolution:
                return self._advance_loop_recovery(outcome, reason)
            return self._retry_or_block(outcome, reason)
        self._uncertain_retries = 0
        if recovering_high_resolution:
            self._recovery_in_progress = RecoveryDirective.HIGH_RESOLUTION
        return SupervisedDecision(DecisionDisposition.EXECUTE, reason, outcome)

    def start_action(
        self,
        outcome: PlannerOutcome,
        snapshot: PerceptionSnapshot,
        frame: Frame,
        *,
        source: str | None = None,
        physical_point: tuple[float, float] | None = None,
        primitives: tuple[str, ...] = (),
        submitted_action_ids: frozenset[str] = frozenset(),
        expected_primitives: int = 0,
    ) -> None:
        if outcome.action is None:
            raise ContractViolation("cannot verify an outcome without an action")
        self._logical_actions_issued += 1
        semantic_key = self._semantic_action_key(outcome.action)
        if semantic_key == self._last_started_action_semantic_key:
            self._consecutive_same_started_action += 1
        else:
            self._last_started_action_semantic_key = semantic_key
            self._consecutive_same_started_action = 1
        action = outcome.action
        point: tuple[float, float] | None = None
        if action.target_box is not None:
            center = action.target_box.center
            point = (
                min(1.0, max(0.0, center.x + action.pointer_offset_x)),
                min(1.0, max(0.0, center.y + action.pointer_offset_y)),
            )
        trace_id = outcome.decision_id
        self._pending = self._pending_action(
            action,
            snapshot,
            frame,
            action_id=trace_id,
            submitted_action_ids=submitted_action_ids,
            expected_primitives=expected_primitives,
        )
        if self._session is not None:
            self._session.record_trace(
                ActionTrace(
                    action_id=trace_id,
                    source=source or "model",
                    proposed_label=action.target_label,
                    proposed_box=_box_tuple(action.target_box),
                    final_label=action.target_label,
                    final_box=_box_tuple(action.target_box),
                    point=point,
                    physical_point=physical_point,
                    primitives=primitives,
                    supervisor_verdict="accepted",
                    submitted=True,
                    effect="pending",
                    detail=str(action.expected_effect),
                    created_at_ns=time.time_ns(),
                    updated_at_ns=time.time_ns(),
                )
            )
        self._journal_row(
            "action_submitted",
            f"{action.kind.value}({action.target_label})",
            (
                f"id={trace_id}; source={source or 'model'}; "
                f"final_box={_box_tuple(action.target_box)}; point={point}; "
                f"physical={physical_point}; primitives={','.join(primitives)}; "
                f"expected={action.expected_effect}"
            ),
            action.target_label,
        )

    def record_execution_receipts(self, receipts: Sequence[ExecutionReceipt]) -> None:
        """Feed the scheduler's terminal primitive receipts into the pending action.

        Receipts for an already-completed or unknown action are ignored and
        each primitive is recorded at most once.  The FIRST executed receipt
        records execution start; the LAST executed receipt anchors effect
        observation. Once every expected primitive has reported,
        the aggregate classifies the execution (executed / partial / rejected
        / expired / flushed) for trace honesty.
        """
        pending = self._pending
        if pending is None or not receipts:
            return
        for receipt in receipts:
            if (
                not pending.submitted_action_ids
                or receipt.action_id not in pending.submitted_action_ids
            ):
                continue
            if any(
                existing.action_id == receipt.action_id for existing in pending.receipts
            ):
                continue
            pending.receipts.append(receipt)
            if receipt.executed:
                pending.executed_primitives += 1
                if pending.completed_at is None or receipt.at > pending.completed_at:
                    pending.completed_at = receipt.at
                if (
                    pending.executed_at is None
                    or receipt.at.value_ns < pending.executed_at.value_ns
                ):
                    pending.executed_at = receipt.at
            if (
                pending.expected_primitives
                and len(pending.receipts) >= pending.expected_primitives
            ):
                pending.execution_status = aggregate_receipts(tuple(pending.receipts))

    def validate_execution_context(
        self, validated_frame: Frame, execution_frame: Frame
    ) -> tuple[bool, str]:
        """Window/geometry identity guard shared by every execution source.

        Recovery exits and calibrated controls skip pixel comparison but
        never skip this: a recreated window must not receive a click decided
        for the previous window.
        """
        if (
            execution_frame.capture_timestamp < validated_frame.capture_timestamp
            or execution_frame.window_identity != validated_frame.window_identity
            or execution_frame.width != validated_frame.width
            or execution_frame.height != validated_frame.height
            or execution_frame.client_rect != validated_frame.client_rect
            or execution_frame.physical_rect != validated_frame.physical_rect
        ):
            self._stale_results_discarded += 1
            return False, "execution frame generation or geometry changed"
        now_ns = self._clock.now().value_ns
        ages_ns = (
            now_ns - validated_frame.capture_timestamp.value_ns,
            now_ns - execution_frame.capture_timestamp.value_ns,
        )
        if any(age < 0 or age > self._max_execution_frame_age_ns for age in ages_ns):
            self._stale_results_discarded += 1
            return False, "execution frame exceeds the freshness budget"
        return True, "execution frame matches the validated window"

    def validate_execution_frame(
        self,
        outcome: PlannerOutcome,
        validated_frame: Frame,
        execution_frame: Frame,
        *,
        target_was_ocr_grounded: bool = False,
    ) -> tuple[bool, str]:
        """Recheck the target immediately before submission to the scheduler."""
        action = outcome.action
        if action is None:
            raise ContractViolation("execution freshness requires an ACT outcome")
        context_stable, context_reason = self.validate_execution_context(
            validated_frame, execution_frame
        )
        if not context_stable:
            return False, context_reason
        if execution_frame.frame_id == validated_frame.frame_id:
            return True, "execution frame is the validated frame"
        region = action.target_box or NormalizedBox(0.0, 0.0, 1.0, 1.0)
        if ActionValidator._target_changed(region, validated_frame, execution_frame):
            if target_was_ocr_grounded:
                return True, "OCR-grounded target tolerated dynamic pixels before execution"
            self._stale_results_discarded += 1
            return False, "target changed after validation and before execution"
        return True, "target remained stable through the execution frame"

    def to_recovery_gui_action(
        self, directive: RecoveryDirective, key_resolver: KeyResolver,
        *, snapshot: PerceptionSnapshot | None = None,
    ) -> GuiAction:
        if directive != RecoveryDirective.BACK:
            raise ContractViolation("only back is a physical recovery directive")
        now = self._clock.now()
        label, hotspot, _box = self._next_exit()
        if hotspot is not None:
            verdict = point_is_clickable(
                Point(hotspot[0], hotspot[1]),
                no_click_regions=self._profile.no_click_regions,
                fresh_text=() if snapshot is None else snapshot.visible_text,
            )
            if verdict.allowed:
                # The calibrated visual exit controls are mouse clicks (top-left
                # ribbon first, then the top-right X as attempts fail); Esc is
                # not a reliable back in this game.
                return GuiAction(
                    f"recovery-{label}-{uuid.uuid4().hex[:12]}",
                    GuiActionKind.CLICK,
                    _action_lifetime(now),
                    x=hotspot[0],
                    y=hotspot[1],
                    confidence=1.0,
                )
            # Fail-closed: a calibrated hotspot that lands inside a forbidden
            # region must fall back to the confirmed key binding, never click.
        key_codes = key_resolver("back")
        if not key_codes:
            raise ContractViolation(
                "visual exit controls are not configured and no confirmed back "
                "key binding exists"
            )
        return GuiAction(
            f"recovery-back-{uuid.uuid4().hex[:12]}",
            GuiActionKind.KEY,
            _action_lifetime(now),
            key_codes=key_codes,
            confidence=1.0,
        )

    def start_recovery_action(
        self,
        snapshot: PerceptionSnapshot,
        frame: Frame,
        *,
        action_id: str | None = None,
        physical_point: tuple[float, float] | None = None,
        primitives: tuple[str, ...] = (),
        submitted_action_ids: frozenset[str] = frozenset(),
        expected_primitives: int = 0,
    ) -> None:
        self._last_started_action_semantic_key = None
        self._consecutive_same_started_action = 0
        label, hotspot, box = self._next_exit()
        if hotspot is not None and box is not None:
            action = GroundedAction(
                GuiActionKind.CLICK,
                label,
                box,
                "leave the current feature page and return to the world view",
                1.0,
            )
        else:
            action = GroundedAction(
                GuiActionKind.KEY,
                "back",
                None,
                "return to a different recoverable page",
                1.0,
                key="back",
            )
        # Mark the back transition in progress regardless of the entry path so
        # its effect observation always feeds the failure streak.
        self._recovery_in_progress = RecoveryDirective.BACK
        if self._on_exit_executed is not None:
            # Tell the planner its tracker-click cooldown just restarted: the
            # whole point of this exit is to re-observe the world page.
            self._on_exit_executed()
        self._logical_actions_issued += 1
        trace_id = action_id or f"recovery-{label}-{uuid.uuid4().hex[:8]}"
        self._pending = self._pending_action(
            action,
            snapshot,
            frame,
            action_id=trace_id,
            submitted_action_ids=submitted_action_ids,
            expected_primitives=expected_primitives,
        )
        if self._session is not None:
            self._session.record_trace(
                ActionTrace(
                    action_id=trace_id,
                    source="recovery_exit",
                    proposed_label=label,
                    proposed_box=_box_tuple(action.target_box),
                    final_label=action.target_label,
                    final_box=_box_tuple(action.target_box),
                    point=hotspot,
                    physical_point=physical_point,
                    primitives=primitives,
                    supervisor_verdict="recovery",
                    submitted=True,
                    effect="pending",
                    detail="visual exit recovery",
                    created_at_ns=time.time_ns(),
                    updated_at_ns=time.time_ns(),
                )
            )
        self._journal_row(
            "action_submitted",
            f"{action.kind.value}({action.target_label})",
            (
                f"id={trace_id}; source=recovery_exit; final_box={_box_tuple(action.target_box)}; "
                f"point={hotspot}; physical={physical_point}; "
                f"primitives={','.join(primitives)}"
            ),
            label,
        )

    @staticmethod
    def _specified_effect(
        pending: _PendingAction, snapshot: PerceptionSnapshot, frame: Frame,
        new_evidence: bool, target_changed: bool,
        ui_state: tuple[tuple[str, bool, bool], ...],
    ) -> tuple[bool, str]:
        spec = pending.action.effect
        assert spec is not None
        if not new_evidence:
            return False, "waiting for a frame captured after the complete operation"
        observed = frozenset(normalize_visible_text(region.text)
                             for region in snapshot.visible_text if region.confidence >= 0.65)
        literal = normalize_visible_text(spec.text or "")
        before = any(literal in text for text in pending.pre_effect_text) if literal else False
        after = any(literal in text for text in observed) if literal else False
        if spec.kind == "text_appears":
            return not before and after, "observed stable new text: " + (spec.text or "")
        if spec.kind == "text_disappears":
            # Empty OCR is unknown, not proof that a label disappeared.
            return before and not after and bool(observed), "observed text disappearance"
        if spec.kind == "target_changes":
            target = normalize_visible_text(pending.action.target_label)
            old = tuple(item for item in pending.ui_state if item[0] == target)
            new = tuple(item for item in ui_state if item[0] == target)
            return target_changed or (bool(old) and old != new), "observed stable target change"
        removed = pending.pre_effect_text - observed
        added = observed - pending.pre_effect_text
        substantial = len(removed) >= 2 and len(added) >= 2
        return (snapshot.mode != pending.mode or (substantial and
                len(removed) >= len(pending.pre_effect_text) / 2)), "observed stable scene change"

    def planner_feedback(self, task_generation: int | None = None) -> str:
        """Bounded executed/effect facts, available even without a game strategy."""
        records = [item for item in self._feedback
                   if task_generation is None or item["task_generation"] == task_generation]
        return json.dumps({"recent_actions": records, "effect_pending": self._pending is not None,
                           "terminal": self.status.value},
                          ensure_ascii=False, separators=(",", ":"))

    def record_decision_feedback(self, decision: SupervisedDecision) -> None:
        action = decision.outcome.action
        if action is not None and decision.disposition not in {
            DecisionDisposition.EXECUTE, DecisionDisposition.RECOVER
        }:
            self._feedback.append({"action": action.kind.value, "target": action.target_label[:80],
                                   "status": decision.disposition.value,
                                   "reason": decision.reason[:240],
                                   "task_generation": decision.outcome.task_generation})

    def to_gui_action(
        self, outcome: PlannerOutcome, key_resolver: KeyResolver
    ) -> GuiAction:
        action = outcome.action
        if action is None:
            raise ContractViolation("only ACT outcomes can become GUI actions")
        now = self._clock.now()
        lifetime = _action_lifetime(now)
        if action.kind in {GuiActionKind.KEY, GuiActionKind.HOTKEY}:
            assert action.key is not None
            key_codes = key_resolver(action.key)
            if not key_codes:
                raise ContractViolation(f"grounded key has no confirmed binding: {action.key}")
            return GuiAction(
                f"grounded-{outcome.decision_id[:12]}",
                action.kind,
                lifetime,
                key_codes=key_codes,
                confidence=min(outcome.confidence, action.confidence),
            )
        assert action.target_box is not None
        center = action.target_box.center
        return GuiAction(
            f"grounded-{outcome.decision_id[:12]}",
            action.kind,
            lifetime,
            x=min(1.0, max(0.0, center.x + action.pointer_offset_x)),
            y=min(1.0, max(0.0, center.y + action.pointer_offset_y)),
            confidence=min(outcome.confidence, action.confidence),
            scroll_delta=action.scroll_delta,
        )

    def fail(self, reason: str) -> None:
        self.status = TerminalStatus.FAILED
        self.termination_reason = reason
        self._complete_task(success=False)

    def _retry_or_block(self, outcome: PlannerOutcome, reason: str) -> SupervisedDecision:
        if self._uncertain_retries < 1:
            self._uncertain_retries += 1
            return SupervisedDecision(DecisionDisposition.REOBSERVE, reason, outcome)
        return self._block(outcome, reason + "; retry exhausted")

    def _block(self, outcome: PlannerOutcome, reason: str) -> SupervisedDecision:
        self._stop_blocked(reason)
        self.goal_confidence = outcome.confidence
        return SupervisedDecision(DecisionDisposition.BLOCK, reason, outcome)

    def _request_recovery(self, reason: str) -> None:
        # F05/F06: every failure recovery — high-resolution re-probe AND the
        # visual back exit — draws from the run-level budget.  A budget of
        # zero refuses both; a rebuilt supervisor inherits the same budget
        # and can never reset the accounting.
        next_kind = (
            RecoveryKind.BACK if self._recovery_count else RecoveryKind.HIGH_RESOLUTION
        )
        if not self._recovery_budget.consume(next_kind):
            state = self._recovery_budget.state()
            self._stop_blocked(
                reason
                + f"; recovery budget exhausted ({state.consumed}/{state.limit} used;"
                " max_recoveries=0 disables every recovery)"
            )
            return
        if self._recovery_count == 0:
            self._recovery_count = 1
            self._pending_recovery = RecoveryDirective.HIGH_RESOLUTION
            self._recovery_in_progress = None
            return
        if "back" not in self._profile.recovery_safe_actions:
            self._stop_blocked(reason + "; no distinct safe recovery remains")
            return
        if self._back_recovery_streak >= self._max_failed_back_recoveries:
            self._stop_blocked(
                reason
                + "; visual back recovery produced no verified effect "
                f"{self._back_recovery_streak} times in a row"
            )
            return
        # Leaving the stuck page via the visual back control is a failure
        # recovery like any other: it draws the run-level budget (F05) so a
        # max_recoveries=0 run can never be steered through back exits.
        self._pending_recovery = RecoveryDirective.BACK
        self._recovery_in_progress = None

    def _advance_loop_recovery(
        self, outcome: PlannerOutcome, reason: str
    ) -> SupervisedDecision:
        if (
            "back" in self._profile.recovery_safe_actions
            and self._back_recovery_streak < self._max_failed_back_recoveries
        ):
            if not self._recovery_budget.consume(RecoveryKind.BACK):
                state = self._recovery_budget.state()
                return self._block(
                    outcome,
                    reason
                    + "; recovery budget exhausted "
                    f"({state.consumed}/{state.limit} used)",
                )
            self._pending_recovery = None
            self._recovery_in_progress = RecoveryDirective.BACK
            return SupervisedDecision(
                DecisionDisposition.RECOVER,
                reason + "; using the visual back control to leave the stuck page",
                outcome,
                RecoveryDirective.BACK,
            )
        return self._block(outcome, reason + "; no safe recovery remains")

    def _record_action_result(
        self,
        pending: _PendingAction,
        after: SemanticState,
        *,
        effect_observed: bool,
        target_changed: bool,
    ) -> LoopFinding | None:
        finding = self._loops.record(
            LoopRecord(
                pending.semantic_state.loop_signature,
                self._action_key(pending.action),
                effect_observed,
                after.loop_signature,
                self._progress.progressed(
                    pending.semantic_state,
                    after,
                    target_effect_observed=target_changed,
                ),
            )
        )
        if finding is not None:
            self.last_loop_finding = finding
        return finding

    def _pending_action(
        self,
        action: GroundedAction,
        snapshot: PerceptionSnapshot,
        frame: Frame,
        *,
        action_id: str | None = None,
        submitted_action_ids: frozenset[str] = frozenset(),
        expected_primitives: int = 0,
    ) -> _PendingAction:
        digest = b"" if action.target_box is None else region_digest(frame, action.target_box)
        return _PendingAction(
            action,
            snapshot.mode,
            stable_anchor_tokens(snapshot.text),
            snapshot.goal_facts,
            tuple(
                (normalize_visible_text(element.label), element.enabled, element.selected)
                for element in snapshot.ui_elements
            ),
            self._progress.state(snapshot),
            digest,
            self._clock.now(),
            anchors=page_anchor_signature(snapshot.visible_text),
            action_id=action_id,
            submitted_action_ids=submitted_action_ids,
            expected_primitives=expected_primitives,
            execution_frame_id=snapshot.frame_id,
            window_identity=snapshot.window_identity,
            geometry_generation=snapshot.geometry_generation,
            task_generation=snapshot.task_generation,
            pre_effect_text=frozenset(normalize_visible_text(region.text)
                                     for region in snapshot.visible_text
                                     if region.confidence >= 0.65),
        )

    def _stop_blocked(self, reason: str) -> None:
        self.status = TerminalStatus.BLOCKED
        self.termination_reason = reason
        self._pending_recovery = None
        self._recovery_in_progress = None
        if self._task_graph is not None and self._task_node_id is not None:
            node = self._task_graph.get(self._task_node_id)
            if node.status in {TaskStatus.READY, TaskStatus.ACTIVE}:
                self._task_graph.block(self._task_node_id)

    def _reset_no_safe_waits(self) -> None:
        self._no_safe_state_signature = None
        self._no_safe_state_repeats = 0
        # Any concrete decision/action also breaks a same-state waiting streak.
        self._wait_state_signature = None
        self._wait_state_repeats = 0

    def _stuck_wait_bound(self, snapshot: PerceptionSnapshot) -> int:
        """Feature pages give up on identical-state waiting much sooner: a
        one-shot quest step (洗髓 0/1) finishes with a single click and the
        page must be left instead of waited on."""
        if self._session is not None and self._session.screen_type == ScreenType.FEATURE:
            return min(4, self._max_stuck_waits)
        return self._max_stuck_waits

    def _stable_state_key(self, snapshot: PerceptionSnapshot) -> str:
        """Animation-proof page identity for repeat counting.

        The perceptual image hash changes on every animated frame, which made
        identical-state wait/no-safe detection reset forever on glowing pages.
        Page anchors plus digit-stripped short-region tokens are stable across
        animation and across scrolling marquees (long sentences excluded).
        """
        short_regions = [
            region for region in snapshot.visible_text if len(region.text) <= 16
        ]
        anchors = ",".join(sorted(page_anchor_signature(short_regions)))
        tokens = ",".join(sorted(stable_anchor_tokens(r.text for r in short_regions)))
        return f"{snapshot.mode.value}|{anchors}|{tokens}"

    def _next_exit(self) -> tuple[str, tuple[float, float] | None, NormalizedBox | None]:
        """Alternate the exit control as attempts fail: top-left ribbon first,
        then the top-right X, then back again.  Both are user-confirmed
        calibrations, so no OCR grounding is required for either."""
        prefer_close = self._back_recovery_streak % 2 == 1
        if prefer_close and self._close_hotspot is not None and self._close_box is not None:
            return "ui_close", self._close_hotspot, self._close_box
        if self._back_hotspot is not None and self._back_box is not None:
            return "ui_back", self._back_hotspot, self._back_box
        if self._close_hotspot is not None and self._close_box is not None:
            return "ui_close", self._close_hotspot, self._close_box
        return "ui_back", None, None

    def _journal_row(
        self,
        kind: str,
        action: str,
        detail: str,
        quest_step: str | None,
    ) -> None:
        if self._journal is None:
            return
        quest = (
            None
            if self._session is None or self._session.latest_main_task is None
            else self._session.latest_main_task.raw_text
        )
        self._journal.record(
            DecisionRecord(
                timestamp=time.time(),
                kind=kind,
                latency_s=None,
                action=action,
                detail=detail,
                quest=quest,
                quest_step=quest_step,
                images=None,
                reply_head=None,
            )
        )

    def _journal_effect(
        self, pending: _PendingAction, effect: str, detail: str
    ) -> None:
        trace_id = pending.action_id or "unknown-action"
        self._feedback.append({
            "action": pending.action.kind.value, "target": pending.action.target_label[:80],
            "status": effect, "execution_status": pending.execution_status,
            "reason": detail[:240], "task_generation": pending.task_generation,
        })
        self._journal_row(
            "action_effect",
            f"{pending.action.kind.value}({pending.action.target_label})",
            f"id={trace_id}; effect={effect}; {detail}",
            None,
        )

    def _finish_trace(
        self, pending: _PendingAction, effect: str, detail: str
    ) -> None:
        if self._session is None or pending.action_id is None:
            return
        self._session.update_trace(
            pending.action_id,
            effect=effect,
            detail=detail,
            updated_at_ns=time.time_ns(),
        )

    def _matches_goal_action_target(self, action: GroundedAction) -> bool:
        if self._goal_action_target is None:
            return False
        label = normalize_visible_text(action.target_label)
        return self._goal_action_target in label or label in self._goal_action_target

    def _is_back_intent(self, action: GroundedAction) -> bool:
        """A label meaning 'leave this page' (never 退出, which may quit the
        whole client).  Model TEXT labels are converted to the alternating
        calibrated hotspots because graphical exit controls carry no OCR text
        and would fail grounding.  A model-claimed ``ui_back``/``ui_close``
        label arrives here too: the reserved label alone is not a trusted
        source, so the click lands on the runtime-owned calibration, never on
        the model's own box."""
        if self._back_hotspot is None and self._close_hotspot is None:
            return False
        if action.kind not in {GuiActionKind.CLICK, GuiActionKind.LONG_CLICK}:
            return False
        label = normalize_visible_text(action.target_label)
        return (
            "返回" in label
            or "回退" in label
            or "关闭" in label
            or label in {"back", "return", "close", "ui_back", "ui_close"}
        )

    def _action_in_recent_failures(self, action: GroundedAction) -> bool:
        """True when the same action recently failed (loop/ineffective ring):
        such a proposal must not cancel an armed page exit."""
        key = self._action_key(action)
        return any(
            record.action_key == key and not record.effect_observed
            for record in self._loops.records
        )

    def _complete_task(self, *, success: bool) -> None:
        if self._task_graph is None or self._task_node_id is None:
            return
        if self._task_graph.get(self._task_node_id).status == TaskStatus.ACTIVE:
            self._task_graph.complete(self._task_node_id, success=success)

    @staticmethod
    def _action_key(action: GroundedAction) -> str:
        box = action.target_box
        location = "none" if box is None else f"{box.center.x:.2f},{box.center.y:.2f}"
        label = re.sub(r"\W+", "", action.target_label.casefold())
        return f"{action.kind.value}:{label}:{location}:{action.scroll_delta}"

    @staticmethod
    def _semantic_action_key(action: GroundedAction) -> str:
        label = re.sub(r"\W+", "", normalize_visible_text(action.target_label))
        return f"{action.kind.value}:{label}:{action.key or ''}:{action.scroll_delta}"

    def _should_escape_repeated_action(self, action: GroundedAction) -> bool:
        if (
            "back" not in self._profile.recovery_safe_actions
            or action.kind not in {GuiActionKind.CLICK, GuiActionKind.LONG_CLICK}
            or self._semantic_action_key(action) != self._last_started_action_semantic_key
        ):
            return False
        if action.target_label in {"ui_back", "ui_close", "ui_promote"}:
            # Exit/promote controls must never trigger another exit escape;
            # their own failure bound lives in the back recovery streak.
            return False
        label = normalize_visible_text(action.target_label)
        if any(term in label for term in ("继续", "确定", "提交", "下一步", "跳过", "领取")):
            return False
        if action.target_box is not None:
            center = action.target_box.center
            if center.x <= 0.28 and 0.20 <= center.y <= 0.38:
                return False
        key = self._semantic_action_key(action)
        expiry = self._escaped_action_keys.get(key)
        now = time.monotonic()
        if expiry is not None:
            if now < expiry:
                # Sticky escape: this action already triggered an exit escape
                # recently — the very next repeat escapes immediately instead
                # of getting two more fresh attempts.
                return True
            del self._escaped_action_keys[key]
        if self._consecutive_same_started_action < 2:
            return False
        self._escaped_action_keys[key] = now + 60.0
        return True


def _action_lifetime(now: UGATime):  # type: ignore[no-untyped-def]
    from uga.control.lifetime import ActionLifetime

    return ActionLifetime(now, now, UGATime(now.value_ns + 1_000_000_000))


_CLOSE_GLYPH_AIM_FRACTION = 0.12
_VISUAL_CLICK_MIN_CONFIDENCE = 0.85


def _hotspot_box(hotspot: tuple[float, float]) -> NormalizedBox:
    """A small verification box centred on the visual back hotspot."""
    x, y = hotspot
    left = max(0.0, x - 0.035)
    top = max(0.0, y - 0.030)
    right = min(1.0, max(left + 0.01, x + 0.035))
    bottom = min(1.0, max(top + 0.01, y + 0.030))
    return NormalizedBox(left, top, right, bottom)


def _box_tuple(box: NormalizedBox | None) -> tuple[float, float, float, float] | None:
    return None if box is None else (box.left, box.top, box.right, box.bottom)


def _digest_difference(before: bytes, after: bytes) -> float:
    if not before or len(before) != len(after):
        return 1.0
    return sum(a != b for a, b in zip(before, after, strict=True)) / len(before)


def _meaningful_text_change(before: frozenset[str], after: frozenset[str]) -> bool:
    """Reject OCR speckle while retaining page-scale semantic transitions."""
    if not before or not after or before == after:
        return False
    changed = before ^ after
    if len(changed) < 2:
        return False
    union = before | after
    similarity = len(before & after) / len(union)
    return similarity < 0.75
