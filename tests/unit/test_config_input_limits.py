from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from uga.core.errors import ContractViolation
from uga.environment.profile import load_game_profile
from uga.models.registry import ModelRole, load_model_registry

VALID_PROFILE = """\
game:
  id: probe
  display_name: Probe
process:
  executable: [probe.exe]
window:
  preferred_capture: auto
controls:
  move_forward: {kind: scan_code, code: 17}
camera:
  type: relative_mouse
  sensitivity: 1.0
capabilities:
  realtime_3d: true
  gui: false
  combat: false
  gamepad: false
safety:
  environment_class: developer_owned
  automation_allowed: true
  multiplayer: false
  anti_cheat_present: false
"""

VALID_REGISTRY = """\
models:
  planner:
    provider: qwen
    model: Qwen3-VL-8B-Thinking
    enabled: true
"""


class ConfigInputLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)

    def test_game_profile_larger_than_the_config_limit_fails_closed(self) -> None:
        path = self.root / "profile.yaml"
        path.write_text(VALID_PROFILE + ("#" * 1024 * 1024) + "\n", encoding="utf-8")
        with self.assertRaises(ContractViolation):
            load_game_profile(path)

    def test_model_registry_larger_than_the_config_limit_fails_closed(self) -> None:
        path = self.root / "models.yaml"
        path.write_text(VALID_REGISTRY + ("#" * 1024 * 1024) + "\n", encoding="utf-8")
        with self.assertRaises(ContractViolation):
            load_model_registry(path)

    def test_small_game_profile_still_loads(self) -> None:
        path = self.root / "profile.yaml"
        path.write_text(VALID_PROFILE, encoding="utf-8")
        profile = load_game_profile(path)
        self.assertEqual(profile.game_id, "probe")

    def test_game_profile_rejects_string_safety_boolean(self) -> None:
        path = self.root / "profile.yaml"
        path.write_text(
            VALID_PROFILE.replace("automation_allowed: true", 'automation_allowed: "false"'),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ContractViolation, "must be a boolean"):
            load_game_profile(path)

    def test_game_profile_rejects_string_binding_confirmation(self) -> None:
        path = self.root / "profile.yaml"
        text = VALID_PROFILE.replace(
            "code: 17}", 'code: 17, confirmed: "false"}'
        )
        path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ContractViolation, "must be a boolean"):
            load_game_profile(path)

    def test_game_profile_rejects_non_finite_camera_sensitivity(self) -> None:
        path = self.root / "profile.yaml"
        path.write_text(
            VALID_PROFILE.replace("sensitivity: 1.0", "sensitivity: .nan"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ContractViolation, "sensitivity must be positive"):
            load_game_profile(path)

    def test_small_model_registry_still_loads(self) -> None:
        path = self.root / "models.yaml"
        path.write_text(VALID_REGISTRY, encoding="utf-8")
        registry = load_model_registry(path)
        self.assertIsNotNone(registry.get(ModelRole.PLANNER))


if __name__ == "__main__":
    unittest.main()
