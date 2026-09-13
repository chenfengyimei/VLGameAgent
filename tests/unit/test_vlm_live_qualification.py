from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from uga.core.errors import ContractViolation
from uga.release.vlm_live_qualification import build_vlm_live_qualification_report

_REQUIRED = {
    "actions.parquet",
    "annotations.jsonl",
    "events.jsonl",
    "metadata.json",
    "metrics.json",
    "observations.parquet",
    "planner.jsonl",
    "provenance.parquet",
    "run.json",
    "tasks.json",
    "timeline.parquet",
    "video.mp4",
}


def _episode(root: Path, episode_id: str, revision: str, *, loop: bool = False) -> None:
    episode = root / episode_id
    episode.mkdir()
    run = {
        "schema": "uga.run",
        "schema_version": "1.1",
        "run_id": episode_id,
        "result": "failure" if loop else "success",
        "termination_reason": "recovery budget exhausted" if loop else "goal_confirmed",
        "source_revision": revision,
        "source_tree_clean": True,
        "model_id": "fixture-vlm",
    }
    metrics = {
        "scheduled_actions": 2 if loop else 3,
        "executed_actions": 2 if loop else 3,
        "logical_actions_issued": 1,
        "verified_effect_actions": 0 if loop else 1,
        "ineffective_actions": 1 if loop else 0,
        "pending_action_at_termination": 0,
        "stale_results_discarded": 1 if loop else 0,
        "max_consecutive_same_ineffective_action": 1 if loop else 0,
        "recovery_count": 2 if loop else 0,
        "capture_gap_p95_ns": 300_000_000,
        "capture_gap_max_ns": 450_000_000,
    }
    decisions = []
    if loop:
        decisions.append(
            {
                "id": "stale",
                "payload": {
                    "disposition": "reobserve",
                    "supervision_reason": "stale decision discarded",
                    "outcome": {"data": {"kind": "act"}},
                },
            }
        )
    else:
        decisions.append(
            {
                "id": "done",
                "payload": {
                    "disposition": "terminate",
                    "supervision_reason": "goal confirmed",
                    "outcome": {"data": {"kind": "done"}},
                },
            }
        )
    decisions.append(
        {
            "id": "terminal",
            "payload": {
                "kind": "terminal",
                "closed_loop": {
                    "status": "blocked" if loop else "succeeded",
                    "last_loop_finding": {"kind": "state_cycle"} if loop else None,
                    "no_safe_state_repeats": 0,
                },
            },
        }
    )
    (episode / "run.json").write_text(json.dumps(run), encoding="utf-8")
    (episode / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (episode / "planner.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in decisions), encoding="utf-8"
    )
    for name in _REQUIRED - {"run.json", "metrics.json", "planner.jsonl"}:
        (episode / name).write_bytes(b"fixture")
    checksums = {
        name: hashlib.sha256((episode / name).read_bytes()).hexdigest()
        for name in sorted(_REQUIRED)
    }
    (episode / "checksum.json").write_text(
        json.dumps({"algorithm": "sha256", "files": checksums}),
        encoding="utf-8",
    )


def _plan(root: Path, revision: str) -> tuple[Path, Path]:
    episodes = root / "episodes"
    episodes.mkdir()
    rows: list[dict[str, object]] = []
    for goal in range(20):
        for repetition in range(5):
            episode_id = f"task-{goal:02d}-{repetition}"
            _episode(episodes, episode_id, revision)
            rows.append(
                {
                    "episode_id": episode_id,
                    "goal_id": f"goal-{goal:02d}",
                    "repetition": repetition,
                    "role": "task",
                    "reviewed": True,
                    "wrong_window": False,
                    "wrong_target": False,
                    "critical_error": False,
                    "loop_detection_rounds": None,
                }
            )
    _episode(episodes, "loop-00", revision, loop=True)
    rows.append(
        {
            "episode_id": "loop-00",
            "goal_id": "loop-goal",
            "repetition": 0,
            "role": "loop",
            "reviewed": True,
            "wrong_window": False,
            "wrong_target": False,
            "critical_error": False,
            "loop_detection_rounds": 2,
        }
    )
    plan = root / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema": "uga.vlm_live_plan",
                "schema_version": "1.1",
                "source_revision": revision,
                "episodes": rows,
            }
        ),
        encoding="utf-8",
    )
    return plan, episodes


class VlmLiveQualificationTests(unittest.TestCase):
    @patch("uga.release.vlm_live_qualification.ReplayEngine")
    def test_builds_passing_100_episode_and_loop_report(self, replay: object) -> None:
        revision = "a" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, episodes = _plan(root, revision)

            report = build_vlm_live_qualification_report(
                plan_path=plan,
                episodes_root=episodes,
                output_path=root / "report.json",
                source_revision=revision,
            )

            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(payload["passed"])
            self.assertEqual(payload["task_episodes"], 100)
            self.assertEqual(payload["task_goals"], 20)
            self.assertEqual(payload["loop_episodes"], 1)
            self.assertEqual(payload["stale_results_discarded"], 1)
            self.assertEqual(len(payload["artifacts"]), 101)
            self.assertTrue(replay.called)  # type: ignore[attr-defined]

    @patch("uga.release.vlm_live_qualification.ReplayEngine")
    def test_accepts_bounded_no_safe_stall_as_injected_loop(self, replay: object) -> None:
        del replay
        revision = "d" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, episodes = _plan(root, revision)
            planner_path = episodes / "loop-00" / "planner.jsonl"
            rows = tuple(
                json.loads(line) for line in planner_path.read_text(encoding="utf-8").splitlines()
            )
            rows[-1]["payload"]["closed_loop"]["last_loop_finding"] = None
            rows[-1]["payload"]["closed_loop"]["no_safe_state_repeats"] = 2
            planner_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            checksum_path = planner_path.parent / "checksum.json"
            checksum = json.loads(checksum_path.read_text(encoding="utf-8"))
            checksum["files"]["planner.jsonl"] = hashlib.sha256(
                planner_path.read_bytes()
            ).hexdigest()
            checksum_path.write_text(json.dumps(checksum), encoding="utf-8")

            report = build_vlm_live_qualification_report(
                plan_path=plan,
                episodes_root=episodes,
                output_path=root / "report.json",
                source_revision=revision,
            )

            self.assertTrue(json.loads(report.read_text(encoding="utf-8"))["passed"])

    @patch("uga.release.vlm_live_qualification.ReplayEngine")
    def test_rejects_episode_without_exact_source_binding(self, replay: object) -> None:
        del replay
        revision = "b" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, episodes = _plan(root, revision)
            run_path = episodes / "task-00-0" / "run.json"
            run = json.loads(run_path.read_text(encoding="utf-8"))
            run["source_revision"] = "c" * 40
            run_path.write_text(json.dumps(run), encoding="utf-8")
            checksum_path = run_path.parent / "checksum.json"
            checksum = json.loads(checksum_path.read_text(encoding="utf-8"))
            checksum["files"]["run.json"] = hashlib.sha256(run_path.read_bytes()).hexdigest()
            checksum_path.write_text(json.dumps(checksum), encoding="utf-8")

            with self.assertRaisesRegex(ContractViolation, "source-bound"):
                build_vlm_live_qualification_report(
                    plan_path=plan,
                    episodes_root=episodes,
                    output_path=root / "report.json",
                    source_revision=revision,
                )


if __name__ == "__main__":
    unittest.main()
