from __future__ import annotations

import contextlib
import json
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from uga.core.artifact_limits import DEFAULT_ARTIFACT_LIMITS, parse_json_text, read_text_limited
from uga.core.errors import ContractViolation
from uga.dataset.builder import build_dataset_manifest
from uga.environment.fixture_world import FixtureScenario, FixtureWorld
from uga.release.fixture_qualification import run_fixture_qualification
from uga.release.revision import require_clean_source_revision
from uga.training.motor_pipeline import export_motor_samples
from uga.windows.backend import Win32WindowBackend, WindowSnapshot

_TRAIN_SCENARIOS = (
    FixtureScenario.EXPLORATION,
    FixtureScenario.REALTIME_CONTROL,
    FixtureScenario.GUI_NAVIGATION,
)
_TEST_SCENARIOS = (FixtureScenario.HELDOUT_DIAGONAL,)


def run_fixture_corpus(
    *,
    project_root: Path,
    output_root: Path,
    train_duration_seconds: float,
    test_duration_seconds: float,
    target_fps: float,
    backend: str,
    allow_physical_input: bool,
    require_release_volume: bool = True,
) -> Path:
    if not allow_physical_input:
        raise ContractViolation("fixture corpus collection requires --allow-physical-input")
    planned_train_hours = train_duration_seconds * len(_TRAIN_SCENARIOS) / 3600
    if require_release_volume and planned_train_hours < 5.0:
        raise ContractViolation("release corpus collection requires at least five train hours")
    # Establish provenance before creating any output. Otherwise an output
    # directory outside an ignored run root could make the checkout dirty and
    # leave a partial artifact even though collection never started.
    revision = require_clean_source_revision(project_root)
    root = output_root.resolve()
    episodes_root = root / "episodes"
    reports_root = root / "reports"
    root.mkdir(parents=True, exist_ok=True)
    episodes_root.mkdir(exist_ok=True)
    reports_root.mkdir(exist_ok=True)
    collected: list[tuple[FixtureScenario, str, str]] = []
    for scenario in (*_TRAIN_SCENARIOS, *_TEST_SCENARIOS):
        duration = train_duration_seconds if scenario in _TRAIN_SCENARIOS else test_duration_seconds
        world = FixtureWorld(scenario=scenario)
        process = _launch_fixture(project_root, world)
        try:
            owned_target = _wait_for_owned_window(process, world.window_title)
            report_path = reports_root / f"{scenario.value}.json"
            run_fixture_qualification(
                title_pattern=f"^{re.escape(world.window_title)}$",
                duration_seconds=duration,
                target_fps=target_fps,
                backend_preference=backend,
                episode_root=episodes_root,
                report_path=report_path,
                allow_physical_input=True,
                exercise_focus_loss=True,
                exercise_emergency_hotkey=True,
                fixture_scenario=scenario.value,
                expected_pid=process.pid,
                expected_identity=owned_target.identity,
                source_revision=revision,
            )
            report = parse_json_text(
                read_text_limited(
                    report_path,
                    DEFAULT_ARTIFACT_LIMITS.max_document_bytes,
                    "fixture corpus report",
                )
            )
            episode_path = Path(str(report["recorder"]["episode_path"]))
            collected.append((scenario, episode_path.name, report_path.name))
        finally:
            _stop_owned_fixture(process)

    inventory_path = root / "dataset-inventory.json"
    inventory_path.write_text(
        json.dumps(_inventory(revision, collected), indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_path = root / "dataset-manifest.json"
    manifest = build_dataset_manifest(inventory_path, episodes_root)
    manifest.write(manifest_path)
    train_episode_paths = tuple(
        episodes_root / episode_name
        for scenario, episode_name, _ in collected
        if scenario in _TRAIN_SCENARIOS
    )
    samples_path = export_motor_samples(train_episode_paths, root / "motor-samples.jsonl")
    summary = {
        "schema": "uga.fixture_corpus",
        "schema_version": "1.1",
        "source_revision": revision,
        "dataset_manifest": str(manifest_path),
        "motor_samples": str(samples_path),
        "hours": manifest.hours(),
        "train_hours": sum(
            item.duration_ns for item in manifest.episodes if item.split.value == "train"
        )
        / 3_600_000_000_000,
        "episodes": [
            {"scenario": scenario.value, "episode": episode, "report": report}
            for scenario, episode, report in collected
        ],
    }
    summary_path = root / "corpus-report.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary_path


def _inventory(
    revision: str,
    collected: list[tuple[FixtureScenario, str, str]],
) -> dict[str, object]:
    categories = {
        FixtureScenario.EXPLORATION: "exploration_navigation",
        FixtureScenario.REALTIME_CONTROL: "interaction",
        FixtureScenario.GUI_NAVIGATION: "gui",
        FixtureScenario.HELDOUT_DIAGONAL: "mixed_long_task",
    }
    return {
        "dataset_id": "uga-fixture-corpus-v1",
        "dataset_version": "1.0.0",
        "source_revision": revision,
        "locked_test_games": [
            FixtureWorld(scenario=scenario).game_id for scenario in _TEST_SCENARIOS
        ],
        "licenses": [
            {
                "id": "uga-developer-owned-fixture",
                "source": "UGA Fixture World generated locally",
                "dataset_license": "project-owner-controlled",
                "distribution_allowed": True,
                "commercial_allowed": True,
                "review_date": date.today().isoformat(),
            }
        ],
        "episodes": [
            {
                "path": episode,
                "session_id": f"fixture-{scenario.value}-session",
                "player_id": (
                    "fixture-train-agent"
                    if scenario in _TRAIN_SCENARIOS
                    else "fixture-heldout-agent"
                ),
                "split": "train" if scenario in _TRAIN_SCENARIOS else "test",
                "category": categories[scenario],
                "license_id": "uga-developer-owned-fixture",
                "instruction_labeled": True,
                "reasoning_labeled": scenario == FixtureScenario.HELDOUT_DIAGONAL,
            }
            for scenario, episode, _ in collected
        ],
    }


def _launch_fixture(project_root: Path, world: FixtureWorld) -> subprocess.Popen[bytes]:
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if pythonw.is_file():
        executable = pythonw
    return subprocess.Popen(
        (
            str(executable),
            "-m",
            "apps.example_game",
            "--scenario",
            world.scenario.value,
        ),
        cwd=project_root.resolve(),
    )


def _wait_for_owned_window(process: subprocess.Popen[bytes], title: str) -> WindowSnapshot:
    windows = Win32WindowBackend()
    deadline = time.monotonic() + 10
    seen_owned_window = False
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ContractViolation(f"fixture process exited before opening {title}")
        owned = next(
            (
                item
                for item in windows.discover()
                if item.identity.pid == process.pid and item.title == title
            ),
            None,
        )
        if owned is not None:
            seen_owned_window = True
            if windows.request_foreground(owned.identity.hwnd):
                return owned
        time.sleep(0.05)
    if seen_owned_window:
        raise ContractViolation(
            f"fixture window opened but Windows denied foreground activation: {title}"
        )
    raise ContractViolation(f"fixture window did not open: {title}")


def _stop_owned_fixture(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)
    if process.poll() is None:
        process.kill()
        process.wait(timeout=5)
