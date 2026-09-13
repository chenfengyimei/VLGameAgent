from __future__ import annotations

import unittest

from uga.control.lifetime import ActionLifetime
from uga.control.physical import KeyboardAction
from uga.core.errors import ContractViolation
from uga.recording.schema import ActionProvenance, EpisodeMetadata, EpisodeResult
from uga.time.clock import UGATime


class EpisodeContractTests(unittest.TestCase):
    def test_episode_end_cannot_precede_start(self) -> None:
        with self.assertRaises(ContractViolation):
            EpisodeMetadata(
                "episode-1",
                "game",
                "1",
                (1280, 720),
                "wgc",
                100,
                "task",
                EpisodeResult.SUCCESS,
                "agent",
                None,
                False,
                99,
            )

    def test_provenance_validates_confidence_and_lifetime(self) -> None:
        lifetime = ActionLifetime(UGATime(100), UGATime(101), UGATime(200))
        action = KeyboardAction("key", lifetime, 0x11, True)
        provenance = ActionProvenance(
            action.action_id,
            "FAST_POLICY",
            "policy-1",
            "checkpoint-1",
            "observation-1",
            "skill-1",
            "task-node-1",
            "PLAY_3D",
            "lease-1",
            0.9,
            False,
            lifetime,
        )
        self.assertEqual(provenance.action_id, action.action_id)

    def test_qualification_source_binding_requires_full_paired_revision(self) -> None:
        values = dict(
            episode_id="episode-1",
            game_id="game",
            game_version="1",
            window_size=(1280, 720),
            capture_backend="wgc",
            start_monotonic_ns=100,
            task="task",
            result=EpisodeResult.IN_PROGRESS,
            agent_version="agent",
            policy_version="policy",
            human_controlled=False,
        )
        with self.assertRaisesRegex(ContractViolation, "paired"):
            EpisodeMetadata(**values, source_revision="a" * 40)
        with self.assertRaisesRegex(ContractViolation, "full Git"):
            EpisodeMetadata(
                **values,
                source_revision="abbreviated",
                source_tree_clean=True,
            )

        metadata = EpisodeMetadata(
            **values,
            source_revision="a" * 40,
            source_tree_clean=True,
            model_id="qwen3-vl-4b-instruct",
        )
        self.assertTrue(metadata.source_tree_clean)


if __name__ == "__main__":
    unittest.main()
