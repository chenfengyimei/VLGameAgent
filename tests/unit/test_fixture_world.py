from __future__ import annotations

import unittest
from pathlib import Path

from uga.environment.fixture_world import FixtureMode, FixtureWorld
from uga.environment.profile import load_game_profile


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


if __name__ == "__main__":
    unittest.main()
