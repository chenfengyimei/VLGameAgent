from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from uga.capture.frame import BufferKind, Frame
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
)
from uga.time.clock import ClockBackend, UGATime


class DecisionDisposition(StrEnum):
    EXECUTE = "execute"
    WAIT = "wait"
    REOBSERVE = "reobserve"
    TERMINATE = "terminate"
    BLOCK = "block"


class TerminalStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SupervisedDecision:
    disposition: DecisionDisposition
    reason: str
    outcome: PlannerOutcome


@dataclass(frozen=True, slots=True)
class EffectObservation:
    pending: bool
    effect_observed: bool | None
    detail: str


@dataclass(slots=True)
class _PendingAction:
    action: GroundedAction
    state_signature: str
    mode: ControlMode
    visible_text: frozenset[str]
    goal_facts: tuple[tuple[str, str], ...]
    ui_state: tuple[tuple[str, bool, bool], ...]
    target_digest: bytes
    issued_at: UGATime


OutcomeVerifier = Callable[[PlannerOutcome, PerceptionSnapshot, Frame, str], bool]
KeyResolver = Callable[[str], tuple[int, ...] | None]


class GoalVerifier:
    def __init__(self, *, confirmation_ns: int = 500_000_000, threshold: float = 0.85) -> None:
        if confirmation_ns < 0 or not 0.0 <= threshold <= 1.0:
            raise ContractViolation("goal verifier configuration is invalid")
        self._confirmation_ns = confirmation_ns
        self._threshold = threshold
        self._candidate: tuple[UGATime, str, frozenset[str]] | None = None

    def consider(self, outcome: PlannerOutcome, snapshot: PerceptionSnapshot) -> bool:
        if (
            outcome.kind != DecisionKind.DONE
            or outcome.goal_status != GoalStatus.SUCCEEDED
            or outcome.confidence < self._threshold
        ):
            self._candidate = None
            return False
        evidence = frozenset(
            normalize_visible_text(value)
            for value in (*outcome.visible_text, *snapshot.text)
            if normalize_visible_text(value)
        )
        if self._candidate is None:
            self._candidate = (snapshot.captured_at, snapshot.frame_id, evidence)
            return False
        timestamp, frame_id, previous = self._candidate
        elapsed = snapshot.captured_at.value_ns - timestamp.value_ns
        consistent = not previous or not evidence or bool(previous & evidence)
        if frame_id != snapshot.frame_id and elapsed >= self._confirmation_ns and consistent:
            self._candidate = None
            return True
        return False


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
        if (
            outcome.request_frame_id != decided_snapshot.frame_id
            or outcome.request_frame_sequence != decided_snapshot.frame_sequence
            or outcome.window_generation
            != decided_snapshot.window_identity.window_generation
            or outcome.geometry_generation != decided_snapshot.geometry_generation
            or outcome.task_generation != decided_snapshot.task_generation
            or fresh_snapshot.frame_sequence < decided_snapshot.frame_sequence
            or outcome.window_generation
            != fresh_snapshot.window_identity.window_generation
            or outcome.geometry_generation != fresh_snapshot.geometry_generation
            or outcome.task_generation != fresh_snapshot.task_generation
            or decided_snapshot.window_identity != fresh_snapshot.window_identity
        ):
            return False, "decision generation became stale"
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
            for region in self._profile.no_click_regions:
                blocked = NormalizedBox(*region)
                if blocked.contains(center):
                    return False, "target center is inside a configured no-click region"
            if self._target_changed(action.target_box, decided_frame, fresh_frame):
                return False, "target pixels changed while the model was deciding"
            if not secondary_verified and not self._ocr_target_consistent(
                action, fresh_snapshot
            ):
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
    def _ocr_target_consistent(
        action: GroundedAction, snapshot: PerceptionSnapshot
    ) -> bool:
        if action.target_box is None or not snapshot.visible_text:
            return True
        target = normalize_visible_text(action.target_label)
        overlapping = tuple(
            region
            for region in snapshot.visible_text
            if action.target_box.intersection_ratio(region.box) >= 0.35
        )
        if not overlapping:
            return True
        for region in overlapping:
            text = normalize_visible_text(region.text)
            labels_match = bool(target and text and (target in text or text in target))
            if labels_match and region.confidence >= 0.5:
                return True
        return False

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
    rows = [
        bytes(payload[row * frame.stride_bytes + left * 4 : row * frame.stride_bytes + right * 4])
        for row in range(top, bottom)
    ]
    return b"".join(rows)[::16]


class ClosedLoopSupervisor:
    """Stateful verifier between a VLM proposal and physical GUI input."""

    def __init__(
        self,
        clock: ClockBackend,
        profile: PerceptionProfile,
        *,
        verifier: OutcomeVerifier | None = None,
    ) -> None:
        self._clock = clock
        self._profile = profile
        self._verifier = verifier
        self._goal = GoalVerifier(
            confirmation_ns=profile.page_stable_ms * 1_000_000,
            threshold=0.85,
        )
        self._validator = ActionValidator(profile)
        self._pending: _PendingAction | None = None
        self._uncertain_retries = 0
        self._ineffective: dict[str, int] = {}
        self.status = TerminalStatus.RUNNING
        self.termination_reason: str | None = None
        self.goal_confidence: float | None = None
        self.last_effect_observed: bool | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status != TerminalStatus.RUNNING

    def observe(self, snapshot: PerceptionSnapshot, frame: Frame) -> EffectObservation:
        pending = self._pending
        if pending is None:
            return EffectObservation(False, None, "no action awaiting verification")
        elapsed_ns = snapshot.captured_at.value_ns - pending.issued_at.value_ns
        semantic_text = frozenset(
            normalize_visible_text(value)
            for value in snapshot.text
            if normalize_visible_text(value)
        )
        ui_state = tuple(
            (normalize_visible_text(element.label), element.enabled, element.selected)
            for element in snapshot.ui_elements
        )
        changed = (
            snapshot.mode != pending.mode
            or semantic_text != pending.visible_text
            or snapshot.goal_facts != pending.goal_facts
            or ui_state != pending.ui_state
        )
        if pending.action.target_box is not None and not changed:
            current_digest = region_digest(frame, pending.action.target_box)
            changed = _digest_difference(pending.target_digest, current_digest) > 0.1
        if changed:
            self._pending = None
            self.last_effect_observed = True
            self._uncertain_retries = 0
            return EffectObservation(False, True, pending.action.expected_effect)
        minimum_ns = 250_000_000
        timeout_ns = self._profile.action_effect_timeout_ms * 1_000_000
        if elapsed_ns < max(minimum_ns, timeout_ns):
            return EffectObservation(True, None, "waiting for the expected visual effect")
        key = self._action_key(pending.action)
        self._ineffective[key] = self._ineffective.get(key, 0) + 1
        self._pending = None
        self.last_effect_observed = False
        return EffectObservation(False, False, "action produced no verified visual effect")

    def assess(
        self,
        outcome: PlannerOutcome,
        decided_snapshot: PerceptionSnapshot,
        fresh_snapshot: PerceptionSnapshot,
        decided_frame: Frame,
        fresh_frame: Frame,
        goal: str,
    ) -> SupervisedDecision:
        if self.is_terminal:
            return SupervisedDecision(
                DecisionDisposition.BLOCK,
                self.termination_reason or "loop already stopped",
                outcome,
            )
        if outcome.kind == DecisionKind.DONE:
            if self._goal.consider(outcome, fresh_snapshot):
                self.status = TerminalStatus.SUCCEEDED
                self.termination_reason = "goal_confirmed"
                self.goal_confidence = outcome.confidence
                return SupervisedDecision(
                    DecisionDisposition.TERMINATE, "goal confirmed on two fresh frames", outcome
                )
            return SupervisedDecision(
                DecisionDisposition.REOBSERVE,
                "goal completion awaits a second fresh frame",
                outcome,
            )
        if outcome.kind == DecisionKind.WAIT:
            self._goal.consider(outcome, fresh_snapshot)
            return SupervisedDecision(
                DecisionDisposition.WAIT,
                f"planner wait: {outcome.wait_reason.value}",  # type: ignore[union-attr]
                outcome,
            )
        if outcome.kind in {DecisionKind.ABSTAIN, DecisionKind.RECOVER}:
            return self._retry_or_block(outcome, "planner did not identify a safe action")
        assert outcome.action is not None
        key = self._action_key(outcome.action)
        if self._ineffective.get(key, 0) >= 2:
            return self._block(outcome, "same grounded action failed effect verification twice")
        confidence = min(outcome.confidence, outcome.action.confidence)
        if confidence <= 0.55:
            return self._retry_or_block(outcome, "decision confidence is at or below 0.55")
        requires_verifier = confidence <= 0.85
        secondary_verified = False
        if requires_verifier:
            if self._verifier is None:
                return self._retry_or_block(outcome, "secondary verifier is unavailable")
            if not self._verifier(outcome, fresh_snapshot, fresh_frame, goal):
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
                    return SupervisedDecision(
                        DecisionDisposition.EXECUTE,
                        "secondary verifier resolved OCR conflict",
                        outcome,
                    )
            return self._retry_or_block(outcome, reason)
        self._uncertain_retries = 0
        return SupervisedDecision(DecisionDisposition.EXECUTE, reason, outcome)

    def start_action(
        self, outcome: PlannerOutcome, snapshot: PerceptionSnapshot, frame: Frame
    ) -> None:
        if outcome.action is None:
            raise ContractViolation("cannot verify an outcome without an action")
        digest = (
            b""
            if outcome.action.target_box is None
            else region_digest(frame, outcome.action.target_box)
        )
        self._pending = _PendingAction(
            outcome.action,
            snapshot.state_signature,
            snapshot.mode,
            frozenset(
                normalize_visible_text(value)
                for value in snapshot.text
                if normalize_visible_text(value)
            ),
            snapshot.goal_facts,
            tuple(
                (normalize_visible_text(element.label), element.enabled, element.selected)
                for element in snapshot.ui_elements
            ),
            digest,
            self._clock.now(),
        )

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
                f"grounded-{uuid.uuid4().hex[:12]}",
                action.kind,
                lifetime,
                key_codes=key_codes,
                confidence=min(outcome.confidence, action.confidence),
            )
        assert action.target_box is not None
        center = action.target_box.center
        return GuiAction(
            f"grounded-{uuid.uuid4().hex[:12]}",
            action.kind,
            lifetime,
            x=center.x,
            y=center.y,
            confidence=min(outcome.confidence, action.confidence),
        )

    def fail(self, reason: str) -> None:
        self.status = TerminalStatus.FAILED
        self.termination_reason = reason

    def _retry_or_block(self, outcome: PlannerOutcome, reason: str) -> SupervisedDecision:
        if self._uncertain_retries < 1:
            self._uncertain_retries += 1
            return SupervisedDecision(DecisionDisposition.REOBSERVE, reason, outcome)
        return self._block(outcome, reason + "; retry exhausted")

    def _block(self, outcome: PlannerOutcome, reason: str) -> SupervisedDecision:
        self.status = TerminalStatus.BLOCKED
        self.termination_reason = reason
        self.goal_confidence = outcome.confidence
        return SupervisedDecision(DecisionDisposition.BLOCK, reason, outcome)

    @staticmethod
    def _action_key(action: GroundedAction) -> str:
        box = action.target_box
        location = "none" if box is None else f"{box.center.x:.2f},{box.center.y:.2f}"
        label = re.sub(r"\W+", "", action.target_label.casefold())
        return f"{action.kind.value}:{label}:{location}"


def _action_lifetime(now: UGATime):  # type: ignore[no-untyped-def]
    from uga.control.lifetime import ActionLifetime

    return ActionLifetime(now, now, UGATime(now.value_ns + 1_000_000_000))


def _digest_difference(before: bytes, after: bytes) -> float:
    if not before or len(before) != len(after):
        return 1.0
    return sum(a != b for a, b in zip(before, after, strict=True)) / len(before)
