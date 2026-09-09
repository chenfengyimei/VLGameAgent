from __future__ import annotations

import time
from pathlib import Path

from uga.benchmark.schema import BenchmarkRun, BenchmarkTask
from uga.core.errors import ContractViolation
from uga.environment.fixture_world import (
    FixtureMode,
    FixtureScenario,
    FixtureWorld,
    fixture_policy_features,
)
from uga.policy.fast_policy import DecoderCheckpoint
from uga.training.artifact import TrainingArtifactManifest, sha256_file


class FixtureBenchmarkEnvironment:
    """Deterministic closed-loop benchmark over one owned Fixture World scenario."""

    def __init__(
        self,
        scenario: FixtureScenario,
        checkpoint: DecoderCheckpoint | None = None,
        policy_artifact_sha256: str | None = None,
    ) -> None:
        self._scenario = scenario
        self._checkpoint = checkpoint
        self._policy_artifact_sha256 = policy_artifact_sha256

    @property
    def game_id(self) -> str:
        return FixtureWorld(scenario=self._scenario).game_id

    def run(self, task: BenchmarkTask, repetition: int) -> BenchmarkRun:
        world = FixtureWorld(scenario=self._scenario)
        initial_distance = world.snapshot.distance_to_target
        action_count = 0
        policy_latencies: list[float] = []
        environment_latencies: list[float] = []
        end_to_end_latencies: list[float] = []
        if self._scenario == FixtureScenario.GUI_NAVIGATION:
            world.set_key("escape", True)
            world.set_key("escape", False)
            action_count += 2
            if world.snapshot.mode == FixtureMode.GUI:
                world.click(world.width / 2, world.height / 2 - 20)
                action_count += 1

        policy_started = time.perf_counter_ns()
        movement_keys = self._movement_keys(world)
        policy_latencies.append((time.perf_counter_ns() - policy_started) / 1_000_000)
        for key in movement_keys:
            world.set_key(key, True)
            action_count += 1

        tick_ns = 16_666_667
        max_ticks = max(1, round(task.timeout_seconds * 1_000_000_000 / tick_ns))
        for _ in range(max_ticks):
            started = time.perf_counter_ns()
            world.tick(tick_ns)
            ended = time.perf_counter_ns()
            latency_ms = (ended - started) / 1_000_000
            environment_latencies.append(latency_ms)
            end_to_end_latencies.append(latency_ms + policy_latencies[-1])
            if world.snapshot.distance_to_target <= 25:
                break
        for key in movement_keys:
            world.set_key(key, False)
            action_count += 1
        world.set_key("e", True)
        action_count += 1
        snapshot = world.snapshot
        human_baseline = initial_distance / world.speed_pixels_per_second + 0.5
        return BenchmarkRun(
            task.task_id,
            task.game_id,
            repetition,
            snapshot.success,
            snapshot.elapsed_ns / 1_000_000_000,
            0,
            0,
            0,
            not snapshot.success,
            0,
            0,
            0,
            action_count,
            0.0,
            tuple(environment_latencies),
            tuple(policy_latencies),
            tuple(policy_latencies),
            tuple(end_to_end_latencies),
            human_baseline,
            task.split,
            self._policy_artifact_sha256,
        )

    def _movement_keys(self, world: FixtureWorld) -> tuple[str, ...]:
        if self._checkpoint is None:
            return world.movement_keys
        snapshot = world.snapshot
        features = fixture_policy_features(
            player_x=snapshot.player_x,
            player_y=snapshot.player_y,
            target_x=snapshot.target_x,
            target_y=snapshot.target_y,
            width=world.width,
            height=world.height,
            success=snapshot.success,
            scenario=self._scenario,
        )
        if len(features) != self._checkpoint.input_dim:
            raise ContractViolation("fixture feature vector does not match decoder checkpoint")
        axes = tuple(
            sum(weight * value for weight, value in zip(row, features, strict=True)) + bias
            for row, bias in zip(
                self._checkpoint.axis_weights,
                self._checkpoint.axis_bias,
                strict=True,
            )
        )
        keys: list[str] = []
        if axes[0] > 0.2:
            keys.append("d")
        elif axes[0] < -0.2:
            keys.append("a")
        if axes[1] > 0.2:
            keys.append("s")
        elif axes[1] < -0.2:
            keys.append("w")
        return tuple(keys)


def fixture_environments(
    checkpoint_path: str | Path | None = None,
    policy_artifact_sha256: str | None = None,
) -> dict[str, FixtureBenchmarkEnvironment]:
    checkpoint = None if checkpoint_path is None else DecoderCheckpoint.load(checkpoint_path)
    environments = tuple(
        FixtureBenchmarkEnvironment(scenario, checkpoint, policy_artifact_sha256)
        for scenario in FixtureScenario
    )
    return {environment.game_id: environment for environment in environments}


def verified_fixture_environments(
    artifact_manifest_path: str | Path,
) -> tuple[dict[str, FixtureBenchmarkEnvironment], str]:
    artifact_path = Path(artifact_manifest_path).resolve()
    artifact = TrainingArtifactManifest.load(artifact_path)
    artifact.verify(artifact_path.parent)
    checkpoint = DecoderCheckpoint.load(artifact_path.parent / artifact.model_file)
    if checkpoint.policy_version != artifact.artifact_id:
        raise ContractViolation("training artifact identity does not match its decoder checkpoint")
    artifact_digest = sha256_file(artifact_path)
    environments = tuple(
        FixtureBenchmarkEnvironment(scenario, checkpoint, artifact_digest)
        for scenario in FixtureScenario
    )
    return ({environment.game_id: environment for environment in environments}, artifact_digest)
