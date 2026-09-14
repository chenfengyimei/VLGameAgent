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
from uga.perception.builder import normalize_visible_text, stable_visible_tokens
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
    pixel_change_candidate_at: UGATime | None = None
    pixel_change_candidate_digest: bytes = b""


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
        self._candidate: tuple[UGATime, str, frozenset[str]] | None = None
        self.last_missing_evidence: tuple[str, ...] = ()
        self.last_evidence_confidence: float | None = None

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
            fallback_evidence=outcome.visible_text,
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
        fallback_evidence: tuple[str, ...] = (),
        decision_confidence: float,
    ) -> bool:
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
        evidence = observed or frozenset(
            normalize_visible_text(value)
            for value in fallback_evidence
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
            if not secondary_verified:
                grounding = self._ocr_target_grounding(action, fresh_snapshot)
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
            if action.target_box.intersection_ratio(region.box) >= 0.35
        )
        if not overlapping:
            return "missing"
        for region in overlapping:
            text = normalize_visible_text(region.text)
            labels_match = bool(target and text and (target in text or text in target))
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
        goal_evidence: tuple[str, ...] = (),
        goal_action_target: str | None = None,
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
        self._pending: _PendingAction | None = None
        self._uncertain_retries = 0
        self._ineffective: dict[str, int] = {}
        self._logical_actions_issued = 0
        self._verified_effect_actions = 0
        self._ineffective_actions = 0
        self._stale_results_discarded = 0
        self._last_ineffective_key: str | None = None
        self._consecutive_same_ineffective = 0
        self._max_consecutive_same_ineffective = 0
        self._no_safe_state_signature: str | None = None
        self._no_safe_state_repeats = 0
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

    @property
    def preferred_action_available(self) -> bool:
        return not self._preferred_action_consumed

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
            "last_effect_observed": self.last_effect_observed,
            "logical_actions_issued": self._logical_actions_issued,
            "verified_effect_actions": self._verified_effect_actions,
            "ineffective_actions": self._ineffective_actions,
            "pending_action": self._pending is not None,
            "stale_results_discarded": self._stale_results_discarded,
            "max_consecutive_same_ineffective_action": (
                self._max_consecutive_same_ineffective
            ),
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
        pending = self._pending
        if pending is None:
            return EffectObservation(False, None, "no action awaiting verification")
        elapsed_ns = snapshot.captured_at.value_ns - pending.issued_at.value_ns
        minimum_ns = 250_000_000
        timeout_ns = self._profile.action_effect_timeout_ms * 1_000_000
        semantic_text = frozenset(stable_visible_tokens(snapshot.text))
        ui_state = tuple(
            (normalize_visible_text(element.label), element.enabled, element.selected)
            for element in snapshot.ui_elements
        )
        semantic_state = self._progress.state(snapshot)
        semantic_text_changed = _meaningful_text_change(
            pending.visible_text, semantic_text
        )
        semantic_changed = (
            snapshot.mode != pending.mode
            or semantic_text_changed
            or snapshot.goal_facts != pending.goal_facts
            or ui_state != pending.ui_state
        )
        target_changed = False
        current_digest = b""
        if pending.action.target_box is not None:
            current_digest = region_digest(frame, pending.action.target_box)
            target_changed = _digest_difference(pending.target_digest, current_digest) > 0.1
        if elapsed_ns < minimum_ns:
            return EffectObservation(True, None, "waiting for the minimum action effect window")
        persistent_target_change = False
        target_still_grounded = (
            ActionValidator._ocr_target_grounding(pending.action, snapshot) == "match"
        )
        if target_changed and not semantic_changed and not target_still_grounded:
            if pending.pixel_change_candidate_at is None:
                pending.pixel_change_candidate_at = snapshot.captured_at
                pending.pixel_change_candidate_digest = current_digest
                return EffectObservation(True, None, "waiting for target pixel change to stabilize")
            stable_ns = (
                snapshot.captured_at.value_ns - pending.pixel_change_candidate_at.value_ns
            )
            candidate_stable = (
                _digest_difference(pending.pixel_change_candidate_digest, current_digest) <= 0.02
            )
            if not candidate_stable:
                pending.pixel_change_candidate_at = snapshot.captured_at
                pending.pixel_change_candidate_digest = current_digest
                return EffectObservation(True, None, "transient target pixels are still changing")
            persistent_target_change = stable_ns >= minimum_ns
            if not persistent_target_change:
                return EffectObservation(True, None, "waiting for target pixel change to persist")
        elif not target_changed or target_still_grounded:
            pending.pixel_change_candidate_at = None
            pending.pixel_change_candidate_digest = b""
        changed = semantic_changed or persistent_target_change
        if changed:
            self._pending = None
            self.last_effect_observed = True
            self._verified_effect_actions += 1
            self._last_ineffective_key = None
            self._consecutive_same_ineffective = 0
            self._reset_no_safe_waits()
            self._uncertain_retries = 0
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
            return EffectObservation(False, True, pending.action.expected_effect)
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
        if outcome.kind != DecisionKind.WAIT or outcome.wait_reason != WaitReason.NO_SAFE_ACTION:
            self._reset_no_safe_waits()
        if outcome.kind == DecisionKind.DONE:
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
            if outcome.wait_reason == WaitReason.NO_SAFE_ACTION:
                state = self._progress.state(fresh_snapshot).loop_signature
                if state == self._no_safe_state_signature:
                    self._no_safe_state_repeats += 1
                else:
                    self._no_safe_state_signature = state
                    self._no_safe_state_repeats = 1
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
            if reason in {
                "decision generation became stale",
                "target no longer exists at the grounded OCR region",
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
        self, outcome: PlannerOutcome, snapshot: PerceptionSnapshot, frame: Frame
    ) -> None:
        if outcome.action is None:
            raise ContractViolation("cannot verify an outcome without an action")
        self._logical_actions_issued += 1
        self._pending = self._pending_action(outcome.action, snapshot, frame)

    def validate_execution_frame(
        self,
        outcome: PlannerOutcome,
        validated_frame: Frame,
        execution_frame: Frame,
    ) -> tuple[bool, str]:
        """Recheck the target immediately before submission to the scheduler."""
        action = outcome.action
        if action is None:
            raise ContractViolation("execution freshness requires an ACT outcome")
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
        if execution_frame.frame_id == validated_frame.frame_id:
            return True, "execution frame is the validated frame"
        region = action.target_box or NormalizedBox(0.0, 0.0, 1.0, 1.0)
        if ActionValidator._target_changed(region, validated_frame, execution_frame):
            self._stale_results_discarded += 1
            return False, "target changed after validation and before execution"
        return True, "target remained stable through the execution frame"

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
        self._logical_actions_issued += 1
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
            frozenset(stable_visible_tokens(snapshot.text)),
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

    def _reset_no_safe_waits(self) -> None:
        self._no_safe_state_signature = None
        self._no_safe_state_repeats = 0

    def _matches_goal_action_target(self, action: GroundedAction) -> bool:
        if self._goal_action_target is None:
            return False
        label = normalize_visible_text(action.target_label)
        return self._goal_action_target in label or label in self._goal_action_target

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
