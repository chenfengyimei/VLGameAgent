"""The final safety gate every action source must pass.

Model replies, OCR rule fast paths, calibrated hotspots and recovery exits all
resolve to one click point and one generation context.  A target label is
display text: it never confers authority.  Trusted determinism comes only from
decision sources assigned by runtime rule code (``ocr_*`` fast paths), and even
those must keep the window/geometry/task generation consistent and land the
FINAL click point (pointer offsets applied) outside no-click regions and
monetization decoys.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from uga.agent.session_state import decoy_click_blocked
from uga.core.errors import ContractViolation
from uga.perception.schema import (
    GroundedAction,
    NormalizedBox,
    PerceptionSnapshot,
    PlannerOutcome,
    TextRegion,
)
from uga.windows.coordinates import Point


def is_trusted_deterministic_source(source: str | None) -> bool:
    """Runtime rule code assigns ``ocr_*`` sources; a model reply says ``model``.

    The source is assigned by trusted Python code that constructed the
    outcome; it is never read back out of model JSON.
    """
    return source is not None and source.startswith("ocr_")


def resolved_click_point(action: GroundedAction) -> Point:
    """The physical click point: box center after pointer offsets and clamping.

    Validation must judge this point, not the box center: a nonzero pointer
    offset can move an otherwise safe center into a forbidden strip.
    """
    if action.target_box is None:
        raise ContractViolation("click point resolution requires a target box")
    center = action.target_box.center
    return Point(
        min(1.0, max(0.0, center.x + action.pointer_offset_x)),
        min(1.0, max(0.0, center.y + action.pointer_offset_y)),
    )


@dataclass(frozen=True, slots=True)
class PointVerdict:
    allowed: bool
    reason: str
    point: Point


def point_is_clickable(
    point: Point,
    *,
    no_click_regions: Iterable[tuple[float, float, float, float]],
    decided_text: Sequence[TextRegion] = (),
    fresh_text: Sequence[TextRegion] = (),
) -> PointVerdict:
    """Final-point gate: forbidden regions and monetization decoys reject."""
    for region in no_click_regions:
        if NormalizedBox(*region).contains(point):
            return PointVerdict(
                False,
                "final click point is inside a configured no-click region",
                point,
            )
    coordinates = (point.x, point.y)
    if decoy_click_blocked(
        tuple(decided_text), coordinates
    ) or decoy_click_blocked(tuple(fresh_text), coordinates):
        return PointVerdict(
            False,
            "final click point overlaps an advertising or monetization decoy",
            point,
        )
    return PointVerdict(True, "final click point is clickable", point)


def generations_consistent(
    outcome: PlannerOutcome,
    decided_snapshot: PerceptionSnapshot,
    fresh_snapshot: PerceptionSnapshot,
) -> tuple[bool, str]:
    """One guard for every action source: the request, window, geometry and
    task generations must match between the decided and the fresh snapshots."""
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
    return True, "decision generation matches the live snapshots"
