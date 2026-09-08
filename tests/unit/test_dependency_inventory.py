from __future__ import annotations

import unittest

from uga.core.errors import ContractViolation
from uga.release.dependencies import parse_cargo_metadata, parse_npm_lock


class DependencyInventoryTests(unittest.TestCase):
    def test_cargo_inventory_excludes_workspace_and_flags_unknown_license(self) -> None:
        records = parse_cargo_metadata(
            {
                "packages": [
                    {
                        "name": "workspace-crate",
                        "version": "0.1.0",
                        "source": None,
                        "license": None,
                    },
                    {
                        "name": "dependency",
                        "version": "1.2.3",
                        "source": "registry+https://example.invalid",
                        "repository": "https://example.invalid/dependency",
                        "license": None,
                    },
                ]
            }
        )
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["review_required"])

    def test_cargo_inventory_rejects_invalid_payload(self) -> None:
        with self.assertRaises(ContractViolation):
            parse_cargo_metadata({"packages": "invalid"})

    def test_npm_inventory_records_locked_build_dependencies(self) -> None:
        records = parse_npm_lock(
            {
                "packages": {
                    "": {"name": "workspace"},
                    "node_modules/typescript": {
                        "version": "7.0.2",
                        "resolved": "https://registry.npmjs.org/typescript/-/typescript-7.0.2.tgz",
                        "license": "Apache-2.0",
                        "dev": True,
                    },
                }
            }
        )
        self.assertEqual(records[0]["name"], "typescript")
        self.assertEqual(records[0]["scope"], "build")
        self.assertFalse(records[0]["review_required"])


if __name__ == "__main__":
    unittest.main()
