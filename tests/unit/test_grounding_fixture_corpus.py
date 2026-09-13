from __future__ import annotations

import unittest
from collections import Counter

from uga.evaluation.grounding_fixture_corpus import grounding_fixture_specs
from uga.perception.schema import DecisionKind


class GroundingFixtureCorpusTests(unittest.TestCase):
    def test_owned_fixture_cohort_has_required_shape_and_safe_terminals(self) -> None:
        specs = grounding_fixture_specs()

        self.assertEqual(len(specs), 200)
        self.assertEqual(
            Counter(spec.category for spec in specs),
            Counter({"ordinary": 80, "terminal": 40, "difficult": 40, "loop": 40}),
        )
        self.assertTrue(
            all(
                spec.expected_box is None
                for spec in specs
                if spec.expected_kind != DecisionKind.ACT
            )
        )
        self.assertTrue(all(spec.frame_count <= 3 for spec in specs))
        self.assertTrue(
            all(spec.forbidden_boxes for spec in specs if spec.category in {"difficult", "loop"})
        )


if __name__ == "__main__":
    unittest.main()
