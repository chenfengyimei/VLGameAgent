from __future__ import annotations

import unittest
from pathlib import Path

from uga.core.config import load_config


class ConfigTests(unittest.TestCase):
    def test_default_config_preserves_latest_state_policy(self) -> None:
        root = Path(__file__).resolve().parents[2]
        config = load_config(root / "configs" / "default.toml")
        self.assertEqual(config.capture.max_pending_observations, 1)
        self.assertEqual(config.capture.backend_preference[0], "windows_graphics_capture")
        self.assertEqual(config.control.scheduler_hz, 30.0)
        self.assertEqual(config.control.emergency_hotkey, "ctrl+shift+f12")
        self.assertEqual(config.mode_router.confirmation_frames, 3)
        self.assertEqual(config.mode_router.transition_confidence, 0.8)
        self.assertEqual(config.policy_cadence.observation_rates_hz, (5.0, 4.0, 2.5, 2.0))
        self.assertEqual(config.policy_cadence.action_horizons, (6, 8, 12, 12))
        self.assertEqual(config.dataset_quality.max_input_gap_ns, 500_000_000)


if __name__ == "__main__":
    unittest.main()
