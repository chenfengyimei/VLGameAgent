from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import frame
from uga.evaluation.grounding_runner import run_grounding_predictions
from uga.gui.schema import GuiActionKind
from uga.perception.builder import PerceptionBuilder
from uga.perception.schema import (
    DecisionKind,
    GoalStatus,
    GroundedAction,
    NormalizedBox,
    PlannerOutcome,
)
from uga.perception.text import NullTextProvider


class _Planner:
    last_schema_valid: bool | None = True
    last_raw_reply: str | None = '{"kind":"act"}'

    def __init__(self) -> None:
        self.calls = 0

    def decide(self, *, snapshot, frames, goal, high_resolution_retry=False):  # type: ignore[no-untyped-def]
        del frames, goal, high_resolution_retry
        self.calls += 1
        return PlannerOutcome(
            "decision",
            snapshot.frame_id,
            snapshot.frame_sequence,
            snapshot.window_identity.window_generation,
            snapshot.geometry_generation,
            snapshot.task_generation,
            DecisionKind.ACT,
            "fixture",
            (),
            GoalStatus.IN_PROGRESS,
            0.99,
            GroundedAction(
                GuiActionKind.CLICK,
                "settings",
                NormalizedBox(0.1, 0.1, 0.3, 0.3),
                "settings opens",
                0.99,
            ),
        )


class GroundingRunnerTests(unittest.TestCase):
    @patch("uga.evaluation.grounding_runner._load_frame")
    def test_writes_and_resumes_source_bound_predictions(self, load_frame) -> None:  # type: ignore[no-untyped-def]
        revision = "a" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "frame.bin"
            image.write_bytes(b"fixture image")
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            annotations = root / "annotations.jsonl"
            annotations.write_text(
                json.dumps(
                    {
                        "sample_id": "sample-1",
                        "category": "ordinary",
                        "goal": "open settings",
                        "frames": [{"path": "frame.bin", "sha256": digest}],
                        "ocr_truth": [],
                        "expected_kind": "act",
                        "expected_action_kind": "click",
                        "expected_bbox": [0.1, 0.1, 0.3, 0.3],
                        "expected_effect": "settings opens",
                        "goal_status": "in_progress",
                        "forbidden_kinds": [],
                        "forbidden_action_kinds": [],
                        "forbidden_boxes": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            load_frame.return_value = frame(1, 1_000_000_000)
            planner = _Planner()
            output = root / "predictions.jsonl"

            run_grounding_predictions(
                annotations_path=annotations,
                output_path=output,
                source_revision=revision,
                model_id="fixture-vlm",
                planner=planner,
                perception_builder=PerceptionBuilder(NullTextProvider()),
                require_qualification_volume=False,
            )
            run_grounding_predictions(
                annotations_path=annotations,
                output_path=output,
                source_revision=revision,
                model_id="fixture-vlm",
                planner=planner,
                perception_builder=PerceptionBuilder(NullTextProvider()),
                require_qualification_volume=False,
            )

            rows = output.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            payload = json.loads(rows[0])
            self.assertEqual(payload["source_revision"], revision)
            self.assertTrue(payload["schema_valid"])
            self.assertEqual(payload["target_bbox"], [0.1, 0.1, 0.3, 0.3])
            self.assertEqual(planner.calls, 1)


if __name__ == "__main__":
    unittest.main()

