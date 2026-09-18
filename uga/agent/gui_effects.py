"""Conservative, bounded postcondition evidence; never trusts a model's success claim."""
from __future__ import annotations

from dataclasses import dataclass, replace

from uga.agent.session_state import stable_anchor_tokens
from uga.capture.frame import BufferKind, Frame
from uga.perception.builder import normalize_visible_text
from uga.perception.schema import GroundedAction, NormalizedBox, PerceptionSnapshot
from uga.perception.visual_digest import digest_difference, region_digest
from uga.safety.action_gate import resolved_click_point

# At most one pending action owns one baseline. Oversized baselines cannot
# qualify effects; no unbounded copy and no exception after submitting input.
_MAX_BASELINE_BYTES = 64 * 1024 * 1024
_FULL_FRAME = NormalizedBox(0.0, 0.0, 1.0, 1.0)


@dataclass(frozen=True, slots=True)
class _Evidence:
    key: tuple[str, ...]
    detail: str
    box: NormalizedBox | None = None
    pixels: bytes = b""


class GuiEffectTracker:
    def __init__(
        self, action: GroundedAction, snapshot: PerceptionSnapshot, frame: Frame
    ) -> None:
        self._action = action
        self._before = snapshot
        self._frame: Frame | None = None
        handle = frame.buffer_handle
        if handle.kind == BufferKind.CPU_BYTES and handle.size_bytes <= _MAX_BASELINE_BYTES:
            # Immutable bytes are shared; reusable bytearray/memoryview buffers
            # are copied once before the capture backend can overwrite them.
            view = handle.readonly_view()
            owned = handle.payload if isinstance(handle.payload, bytes) else bytes(view)
            self._frame = replace(frame, buffer_handle=replace(handle, payload=owned))
        self._candidate: _Evidence | None = None
        self._candidate_frame: str | None = None
        self._candidate_ns = 0

    def consider(
        self, snapshot: PerceptionSnapshot, frame: Frame, *, new_evidence: bool
    ) -> tuple[bool, str]:
        before = self._frame
        if (not new_evidence or before is None or snapshot.frame_id != frame.frame_id
                or snapshot.captured_at != frame.capture_timestamp
                or frame.window_identity != before.window_identity
                or (frame.width, frame.height, frame.client_rect, frame.physical_rect)
                != (before.width, before.height, before.client_rect, before.physical_rect)):
            self._candidate = None
            return False, "waiting for matching post-execution frame evidence"
        evidence = self._evidence(snapshot, frame)
        if evidence is None:
            self._candidate = None
            return False, "postcondition is not supported by local visual evidence"
        previous = self._candidate
        stable = previous is not None and previous.key == evidence.key
        if stable and previous is not None:
            if previous.box is not None and evidence.box is not None:
                stable = min(previous.box.intersection_ratio(evidence.box),
                             evidence.box.intersection_ratio(previous.box)) >= 0.5
            if evidence.pixels:
                stable = stable and digest_difference(previous.pixels, evidence.pixels) <= 0.02
        if not stable:
            self._candidate = evidence
            self._candidate_frame = snapshot.frame_id
            self._candidate_ns = snapshot.captured_at.value_ns
            return False, "waiting for a second consistent postcondition observation"
        confirmed = (snapshot.frame_id != self._candidate_frame
                     and snapshot.captured_at.value_ns - self._candidate_ns >= 100_000_000)
        return confirmed, evidence.detail

    def _changed(self, frame: Frame, box: NormalizedBox) -> bool:
        assert self._frame is not None
        return digest_difference(region_digest(self._frame, box), region_digest(frame, box)) > 0.1

    def _evidence(self, snapshot: PerceptionSnapshot, frame: Frame) -> _Evidence | None:
        spec = self._action.effect
        assert spec is not None
        literal = normalize_visible_text(spec.text or "")
        old_text = tuple(self._before.visible_text)
        observed = tuple(region for region in snapshot.visible_text if region.confidence >= 0.65)
        if spec.kind == "text_appears":
            # Low-confidence pre-existing text is uncertainty, not proof that
            # the same string has newly appeared when OCR confidence improves.
            if any(literal in normalize_visible_text(region.text) for region in old_text):
                return None
            matches = [region for region in observed
                       if literal in normalize_visible_text(region.text)
                       and self._changed(frame, region.box)]
            if len(matches) != 1:
                return None
            return _Evidence((spec.kind, literal), "observed stable new text: " + (spec.text or ""),
                             matches[0].box)
        if spec.kind == "text_disappears":
            # OCR dropping/relabeling a box cannot prove pixels disappeared.
            if not observed or any(literal in normalize_visible_text(region.text)
                                   for region in snapshot.visible_text):
                return None
            originals = [region for region in old_text if region.confidence >= 0.65
                         and literal in normalize_visible_text(region.text)]
            if not originals or not all(self._changed(frame, region.box) for region in originals):
                return None
            return _Evidence((spec.kind, literal), "observed text disappearance", originals[0].box)
        if spec.kind == "target_changes":
            box = self._action.target_box
            if box is None:
                return None
            point = resolved_click_point(self._action)
            label = normalize_visible_text(self._action.target_label)

            def state(value: PerceptionSnapshot) -> tuple[str, ...]:
                return tuple(sorted(
                    f"{element.enabled}:{element.selected}"
                    for element in value.ui_elements if element.confidence >= 0.65
                    and element.box.contains(point)
                    and normalize_visible_text(element.label) == label
                ))

            old_state, new_state = state(self._before), state(snapshot)
            if old_state and new_state and old_state != new_state:
                return _Evidence((spec.kind, *new_state),
                                 "observed stable target state change", box)
            if self._changed(frame, box):
                return _Evidence((spec.kind,), "observed stable target pixel change", box,
                                 region_digest(frame, box))
            return None
        before_text = stable_anchor_tokens(region.text for region in old_text
                                           if region.confidence >= 0.65)
        after_text = stable_anchor_tokens(region.text for region in observed)
        removed, added = before_text - after_text, after_text - before_text
        substantial = (len(removed) >= 2 and len(added) >= 2
                       and len(removed) >= len(before_text) / 2)
        if ((snapshot.mode != self._before.mode or substantial)
                and self._changed(frame, _FULL_FRAME)):
            return _Evidence((spec.kind, snapshot.mode.value,
                              *sorted(stable_anchor_tokens(region.text for region in observed))),
                             "observed stable scene change")
        return None
