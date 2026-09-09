from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from uga.benchmark.io import load_benchmark_runs
from uga.core.artifact_limits import parse_json_text
from uga.core.errors import ContractViolation
from uga.release.qualification import QualificationLedger


def _deep_payload(depth: int) -> str:
    return "[" * depth + "]" * depth


class ParseJsonTextTests(unittest.TestCase):
    def test_normal_json_parses_identically(self) -> None:
        text = json.dumps({"kind": "observation", "values": [1, 2, 3]})
        self.assertEqual(parse_json_text(text), json.loads(text))

    def test_empty_and_scalar_documents_parse(self) -> None:
        self.assertEqual(parse_json_text("null"), None)
        self.assertEqual(parse_json_text("  7  "), 7)

    def test_deeply_nested_payload_fails_closed(self) -> None:
        with self.assertRaises(ContractViolation):
            parse_json_text(_deep_payload(100_000))

    def test_decode_errors_still_propagate_as_json_errors(self) -> None:
        with self.assertRaises(ValueError):
            parse_json_text("{not-json")


class UntrustedJsonSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)

    def test_benchmark_deep_nesting_fails_closed(self) -> None:
        path = self.root / "runs.jsonl"
        path.write_text(_deep_payload(100_000) + "\n", encoding="utf-8")
        with self.assertRaises(ContractViolation):
            load_benchmark_runs(path)

    def test_oversized_qualification_ledger_fails_closed(self) -> None:
        path = self.root / "qualification.json"
        path.write_text(" " * (17 * 1024 * 1024), encoding="utf-8")
        with self.assertRaises(ContractViolation):
            QualificationLedger.load(path)


if __name__ == "__main__":
    unittest.main()
