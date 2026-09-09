from __future__ import annotations

import time

from uga.benchmark.schema import BenchmarkRun, BenchmarkTask
from uga.environment.fixture_world import FixtureMode, FixtureScenario, FixtureWorld


class FixtureBenchmarkEnvironment:
    """Deterministic closed-loop benchmark over one owned Fixture World scenario."""

    def __init__(self, scenario: FixtureScenario) -> None:
        self._scenario = scenario

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
        movement_keys = world.movement_keys
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
        )


def fixture_environments() -> dict[str, FixtureBenchmarkEnvironment]:
    environments = tuple(FixtureBenchmarkEnvironment(scenario) for scenario in FixtureScenario)
    return {environment.game_id: environment for environment in environments}
