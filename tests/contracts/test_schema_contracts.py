from __future__ import annotations

import unittest

from tests.helpers import frame, identity
from uga.core.errors import ContractViolation
from uga.core.schema import SCHEMA_VERSION, require_schema_version
from uga.time.clock import INT64_MAX, UGATime


class SchemaContractTests(unittest.TestCase):
    def test_uga_time_is_versioned_non_negative_int64(self) -> None:
        timestamp = UGATime(123)
        envelope = timestamp.to_envelope()
        self.assertEqual(envelope["schema_version"], SCHEMA_VERSION)
        self.assertEqual(require_schema_version(envelope, "uga.time"), {"value_ns": 123})
        with self.assertRaises(ContractViolation):
            UGATime(-1)
        with self.assertRaises(ContractViolation):
            UGATime(INT64_MAX + 1)

    def test_window_identity_envelope_has_generation(self) -> None:
        envelope = identity(generation=7).to_envelope()
        data = require_schema_version(envelope, "uga.window_identity")
        self.assertEqual(data["window_generation"], 7)
        self.assertEqual(data["hwnd"], 100)

    def test_frame_serialization_excludes_buffer_payload(self) -> None:
        envelope = frame(1).to_envelope()
        data = require_schema_version(envelope, "uga.frame")
        self.assertNotIn("payload", data["buffer_handle"])
        self.assertEqual(data["capture_timestamp_ns"], 1)

    def test_unsupported_schema_version_fails_closed(self) -> None:
        envelope = UGATime(1).to_envelope()
        envelope["schema_version"] = "0.9"
        with self.assertRaises(ContractViolation):
            require_schema_version(envelope, "uga.time")


if __name__ == "__main__":
    unittest.main()
