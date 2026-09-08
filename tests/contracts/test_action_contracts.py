from __future__ import annotations

import unittest

from uga.control.canonical import CanonicalAction
from uga.control.lifetime import ActionLifetime
from uga.control.physical import KeyboardAction
from uga.control.semantic import SemanticAction, SemanticActionKind
from uga.core.errors import ContractViolation
from uga.time.clock import UGATime


def lifetime(start: int = 10, end: int = 20) -> ActionLifetime:
    return ActionLifetime(UGATime(start), UGATime(start), UGATime(end))


class ActionContractTests(unittest.TestCase):
    def test_all_three_action_layers_are_versioned_and_lifetime_bound(self) -> None:
        semantic = SemanticAction(
            "semantic-1", SemanticActionKind.INTERACT_WITH_TARGET, "npc", (), lifetime()
        )
        canonical = CanonicalAction("canonical-1", lifetime(), interact=True)
        physical = KeyboardAction("physical-1", lifetime(), 0x21, True)
        self.assertEqual(semantic.to_envelope()["schema_version"], "1.1")
        self.assertEqual(canonical.to_envelope()["schema_version"], "1.1")
        self.assertEqual(physical.to_envelope()["schema_version"], "1.1")

    def test_lifetime_cannot_be_empty_or_reversed(self) -> None:
        with self.assertRaises(ContractViolation):
            ActionLifetime(UGATime(5), UGATime(4), UGATime(6))
        with self.assertRaises(ContractViolation):
            ActionLifetime(UGATime(5), UGATime(6), UGATime(6))

    def test_canonical_axes_are_bounded(self) -> None:
        with self.assertRaises(ContractViolation):
            CanonicalAction("bad", lifetime(), move_x=1.1)


if __name__ == "__main__":
    unittest.main()
