from __future__ import annotations

import unittest
from pathlib import Path

from uga.environment.fixture_world import FixtureMode, FixtureScenario, FixtureWorld
from uga.environment.profile import load_game_profile
from uga.safety.environment_policy import PolicyReason, evaluate_environment


class FixtureWorldTests(unittest.TestCase):
    def test_world_moves_switches_modes_and_completes_goal(self) -> None:
        world = FixtureWorld()
        initial_x = world.snapshot.player_x
        world.set_key("d", True)
        world.tick(1_000_000_000)
        world.set_key("d", False)
        self.assertGreater(world.snapshot.player_x, initial_x)
        world.set_key("escape", True)
        self.assertEqual(world.snapshot.mode, FixtureMode.GUI)
        world.set_key("escape", False)
        world.click(world.width / 2, world.height / 2 - 20)
        self.assertEqual(world.snapshot.mode, FixtureMode.PLAY_3D)
        world.set_key("d", True)
        for _ in range(10):
            world.tick(250_000_000)
        world.set_key("d", False)
        world.set_key("e", True)
        self.assertTrue(world.snapshot.success)
        self.assertEqual(world.snapshot.mode, FixtureMode.COMPLETE)

    def test_fixture_profile_has_exact_window_title_guard(self) -> None:
        root = Path(__file__).resolve().parents[2]
        profile = load_game_profile(root / "configs" / "games" / "uga-fixture-world.yaml")
        self.assertTrue(profile.matches_window_title("UGA Fixture World"))
        self.assertFalse(profile.matches_window_title("unrelated python window"))

    def test_bundled_online_game_profile_is_not_automation_enabled(self) -> None:
        root = Path(__file__).resolve().parents[2]
        profile = load_game_profile(root / "configs" / "games" / "mumu-xianyu.yaml")

        decision = evaluate_environment(profile.safety)

        self.assertFalse(decision.allowed)
        self.assertTrue(profile.safety.multiplayer)
        self.assertEqual(decision.reason, PolicyReason.AUTOMATION_DISABLED)

    def test_each_scenario_has_distinct_identity_and_reachable_goal(self) -> None:
        game_ids: set[str] = set()
        for scenario in FixtureScenario:
            world = FixtureWorld(scenario=scenario)
            game_ids.add(world.game_id)
            for key in world.movement_keys:
                world.set_key(key, True)
            for _ in range(300):
                if world.snapshot.distance_to_target <= 25:
                    break
                world.tick(16_666_667)
            for key in world.movement_keys:
                world.set_key(key, False)
            world.set_key("e", True)
            self.assertTrue(world.snapshot.success, scenario.value)
        self.assertEqual(len(game_ids), len(FixtureScenario))


if __name__ == "__main__":
    unittest.main()
