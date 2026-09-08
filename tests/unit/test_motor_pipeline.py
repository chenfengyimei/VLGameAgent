from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from uga.core.errors import ContractViolation
from uga.training.motor_pipeline import load_motor_samples, load_motor_training_config


class MotorPipelineTests(unittest.TestCase):
    def test_default_config_and_samples_are_loadable(self) -> None:
        root = Path(__file__).resolve().parents[2]
        config = load_motor_training_config(root / "configs" / "training" / "motor_bc.yaml")
        self.assertEqual(config.stage, "motor_bc")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "samples.jsonl"
            path.write_text(
                '{"features":[1,0],"move_x":1,"move_y":0,"look_x":0,"look_y":0,'
                '"buttons":0,"episode_id":"episode-1","observation_id":"obs-1",'
                '"action_id":"action-1"}\n',
                encoding="utf-8",
            )
            samples = load_motor_samples(path)
            self.assertEqual(samples[0].features, (1.0, 0.0))

    def test_empty_samples_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "samples.jsonl"
            path.write_text("", encoding="utf-8")
            with self.assertRaises(ContractViolation):
                load_motor_samples(path)


if __name__ == "__main__":
    unittest.main()
