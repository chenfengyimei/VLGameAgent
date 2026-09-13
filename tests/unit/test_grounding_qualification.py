from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from uga.core.errors import ContractViolation
from uga.evaluation.grounding_qualification import (
    build_grounding_qualification_report,
    load_grounding_corpus,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _cohort(root: Path, revision: str) -> tuple[Path, Path]:
    frames = root / "frames"
    frames.mkdir()
    frame = frames / "screen.bin"
    frame.write_bytes(b"owned fixture pixels")
    digest = hashlib.sha256(frame.read_bytes()).hexdigest()
    annotations: list[dict[str, object]] = []
    predictions: list[dict[str, object]] = []
    categories = (
        ("ordinary", 80, "act"),
        ("terminal", 40, "done"),
        ("difficult", 40, "act"),
        ("loop", 40, "abstain"),
    )
    for category, count, kind in categories:
        for index in range(count):
            sample_id = f"{category}-{index:03d}"
            is_act = kind == "act"
            annotations.append(
                {
                    "sample_id": sample_id,
                    "category": category,
                    "goal": "打开设置",
                    "frames": [{"path": "frames/screen.bin", "sha256": digest}],
                    "ocr_truth": ["设置"],
                    "expected_kind": kind,
                    "expected_action_kind": "click" if is_act else None,
                    "expected_bbox": [0.1, 0.1, 0.3, 0.3] if is_act else None,
                    "expected_effect": "设置页面可见" if is_act else "不产生输入",
                    "goal_status": "succeeded" if kind == "done" else "unknown",
                    "forbidden_kinds": [],
                    "forbidden_action_kinds": [],
                    "forbidden_boxes": [[0.8, 0.8, 1.0, 1.0]],
                }
            )
            predictions.append(
                {
                    "sample_id": sample_id,
                    "source_revision": revision,
                    "model_id": "fixture-vlm",
                    "schema_valid": True,
                    "kind": kind,
                    "visible_text": ["設置" if index == 0 else "设置"],
                    "action_kind": "click" if is_act else None,
                    "target_bbox": [0.11, 0.11, 0.29, 0.29] if is_act else None,
                    "wrong_window": False,
                }
            )
    annotation_path = root / "annotations.jsonl"
    prediction_path = root / "predictions.jsonl"
    _write_jsonl(annotation_path, annotations)
    _write_jsonl(prediction_path, predictions)
    return annotation_path, prediction_path


class GroundingQualificationTests(unittest.TestCase):
    def test_builds_passing_source_bound_200_sample_report(self) -> None:
        revision = "a" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            annotations, predictions = _cohort(root, revision)

            report = build_grounding_qualification_report(
                annotations_path=annotations,
                predictions_path=predictions,
                output_path=root / "report.json",
                source_revision=revision,
            )

            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "uga.vlm_grounding_qualification")
            self.assertEqual(payload["source_revision"], revision)
            self.assertTrue(payload["qualification_volume"])
            self.assertTrue(payload["passed"])
            self.assertEqual(payload["metrics"]["samples"], 200)
            self.assertEqual(payload["metrics"]["wrong_window_count"], 0)

    def test_invalid_schema_is_measured_instead_of_fabricated(self) -> None:
        revision = "b" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            annotations, predictions = _cohort(root, revision)
            rows = [
                json.loads(line)
                for line in predictions.read_text(encoding="utf-8").splitlines()
            ]
            rows[0]["schema_valid"] = False
            _write_jsonl(predictions, rows)

            report = build_grounding_qualification_report(
                annotations_path=annotations,
                predictions_path=predictions,
                output_path=root / "report.json",
                source_revision=revision,
            )
            payload = json.loads(report.read_text(encoding="utf-8"))

            self.assertFalse(payload["passed"])
            self.assertEqual(payload["metrics"]["schema_valid_rate"], 199 / 200)

    def test_rejects_missing_required_category_volume(self) -> None:
        revision = "c" * 40
        with tempfile.TemporaryDirectory() as temporary:
            annotations, _ = _cohort(Path(temporary), revision)
            rows = [
                json.loads(line)
                for line in annotations.read_text(encoding="utf-8").splitlines()
                if '"loop"' not in line
            ]
            _write_jsonl(annotations, rows)

            with self.assertRaisesRegex(ContractViolation, "at least 200"):
                load_grounding_corpus(annotations)


if __name__ == "__main__":
    unittest.main()
