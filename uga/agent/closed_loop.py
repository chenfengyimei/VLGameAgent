from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from uga.agent.progress import (
    LoopDetector,
    LoopFinding,
    LoopRecord,
    ProgressTracker,
    SemanticState,
)
from uga.agent.task_graph import TaskGraph, TaskStatus
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
        max_recoveries: int = 2,
        task_graph: TaskGraph | None = None,
        task_node_id: str | None = None,
    ) -> None:
        if not 0 <= max_recoveries <= 2:
            raise ContractViolation("closed-loop recoveries must be within [0, 2]")
        if (task_graph is None) != (task_node_id is None):
            raise ContractViolation("task graph and active node must be configured together")
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
        self._progress = ProgressTracker()
        self._loops = LoopDetector()
        self._max_recoveries = max_recoveries
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
    def recovery_count(self) -> int:
        return self._recovery_count

    @property
    def high_resolution_retry(self) -> bool:
        return (
            self._pending_recovery == RecoveryDirective.HIGH_RESOLUTION
            or self._uncertain_retries == 1
        )

    def diagnostics(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "termination_reason": self.termination_reason,
            "goal_confidence": self.goal_confidence,
            "recovery_count": self._recovery_count,
            "last_effect_observed": self.last_effect_observed,
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
        semantic_state = self._progress.state(snapshot)
        semantic_changed = (
            snapshot.mode != pending.mode
            or semantic_text != pending.visible_text
            or snapshot.goal_facts != pending.goal_facts
            or ui_state != pending.ui_state
        )
        target_changed = False
        if pending.action.target_box is not None:
            current_digest = region_digest(frame, pending.action.target_box)
            target_changed = _digest_difference(pending.target_digest, current_digest) > 0.1
        changed = semantic_changed or target_changed
        if changed:
            self._pending = None
            self.last_effect_observed = True
            self._uncertain_retries = 0
            finding = self._record_action_result(
                pending,
                semantic_state,
                effect_observed=True,
                target_changed=target_changed,
            )
            if finding is not None:
                self._last_failed_action_key = self._action_key(pending.action)
                self._request_recovery(finding.detail)
            else:
                self._recovery_in_progress = None
                self._last_failed_action_key = None
            self._ineffective.pop(self._action_key(pending.action), None)
            return EffectObservation(False, True, pending.action.expected_effect)
        minimum_ns = 250_000_000
        timeout_ns = self._profile.action_effect_timeout_ms * 1_000_000
        if elapsed_ns < max(minimum_ns, timeout_ns):
            return EffectObservation(True, None, "waiting for the expected visual effect")
        key = self._action_key(pending.action)
        self._ineffective[key] = self._ineffective.get(key, 0) + 1
        self._pending = None
        self.last_effect_observed = False
        finding = self._record_action_result(
            pending,
            semantic_state,
            effect_observed=False,
            target_changed=False,
        )
        self._last_failed_action_key = key
        if self._recovery_in_progress == RecoveryDirective.BACK:
            self._stop_blocked("safe back recovery produced no verified effect")
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
    ) -> SupervisedDecision:
        if self.is_terminal:
            return SupervisedDecision(
                DecisionDisposition.BLOCK,
                self.termination_reason or "loop already stopped",
                outcome,
            )
        recovery = self._pending_recovery
        recovering_high_resolution = recovery == RecoveryDirective.HIGH_RESOLUTION
        if recovering_high_resolution:
            self._pending_recovery = None
        if outcome.kind == DecisionKind.DONE:
            if self._goal.consider(outcome, fresh_snapshot):
                self.status = TerminalStatus.SUCCEEDED
                self.termination_reason = "goal_confirmed"
                self.goal_confidence = outcome.confidence
                self._complete_task(success=True)
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
        if recovery == RecoveryDirective.BACK:
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
        requires_verifier = confidence <= 0.85
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
        self, outcome: PlannerOutcome, snapshot: PerceptionSnapshot, frame: Frame
    ) -> None:
        if outcome.action is None:
            raise ContractViolation("cannot verify an outcome without an action")
        self._pending = self._pending_action(outcome.action, snapshot, frame)

    def to_recovery_gui_action(
        self, directive: RecoveryDirective, key_resolver: KeyResolver
    ) -> GuiAction:
        if directive != RecoveryDirective.BACK:
            raise ContractViolation("only back is a physical recovery directive")
        key_codes = key_resolver("back")
        if not key_codes:
            raise ContractViolation("safe back recovery has no confirmed binding")
        now = self._clock.now()
        return GuiAction(
            f"recovery-back-{uuid.uuid4().hex[:12]}",
            GuiActionKind.KEY,
            _action_lifetime(now),
            key_codes=key_codes,
            confidence=1.0,
        )

    def start_recovery_action(
        self, snapshot: PerceptionSnapshot, frame: Frame
    ) -> None:
        action = GroundedAction(
            GuiActionKind.KEY,
            "back",
            None,
            "return to a different recoverable page",
            1.0,
            key="back",
        )
        self._pending = self._pending_action(action, snapshot, frame)

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
        if self._recovery_count >= self._max_recoveries:
            self._stop_blocked(reason + "; recovery budget exhausted")
            return
        if self._recovery_count == 0:
            directive = RecoveryDirective.HIGH_RESOLUTION
        elif "back" in self._profile.recovery_safe_actions:
            directive = RecoveryDirective.BACK
        else:
            self._stop_blocked(reason + "; no distinct safe recovery remains")
            return
        self._recovery_count += 1
        self._pending_recovery = directive
        self._recovery_in_progress = None

    def _advance_loop_recovery(
        self, outcome: PlannerOutcome, reason: str
    ) -> SupervisedDecision:
        if (
            self._recovery_count < self._max_recoveries
            and "back" in self._profile.recovery_safe_actions
        ):
            self._recovery_count += 1
            self._pending_recovery = None
            self._recovery_in_progress = RecoveryDirective.BACK
            return SupervisedDecision(
                DecisionDisposition.RECOVER,
                reason + "; using configured safe back recovery",
                outcome,
                RecoveryDirective.BACK,
            )
        return self._block(outcome, reason + "; recovery budget exhausted")

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
    ) -> _PendingAction:
        digest = b"" if action.target_box is None else region_digest(frame, action.target_box)
        return _PendingAction(
            action,
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
            self._progress.state(snapshot),
            digest,
            self._clock.now(),
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
        return f"{action.kind.value}:{label}:{location}"


def _action_lifetime(now: UGATime):  # type: ignore[no-untyped-def]
    from uga.control.lifetime import ActionLifetime

    return ActionLifetime(now, now, UGATime(now.value_ns + 1_000_000_000))


def _digest_difference(before: bytes, after: bytes) -> float:
    if not before or len(before) != len(after):
        return 1.0
    return sum(a != b for a, b in zip(before, after, strict=True)) / len(before)
