from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from uga.core.errors import ContractViolation
from uga.policy.vlm_planner import OpenAICompatibleVisionClient


class ModelPolicyTests(unittest.TestCase):
    def test_glm_contract_refuses_disabled_thinking_before_network_access(self) -> None:
        for extra in (None, {"thinking": {"type": "disabled"}}, {"enable_thinking": False}):
            with self.subTest(extra=extra), self.assertRaises(ContractViolation):
                OpenAICompatibleVisionClient(
                    base_url="https://example.invalid/v1", model="glm-5.3-flash",
                    disable_thinking=extra is None, max_output_tokens=4096, extra_body=extra,
                )

    def test_glm_policy_uses_enabled_thinking_and_explicit_output_budget(self) -> None:
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return None
            def read(self, limit):
                return json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()
        client = OpenAICompatibleVisionClient(
            base_url="https://example.invalid/v1", model="glm-5.3-flash", max_output_tokens=4096,
        )
        with patch("uga.policy.vlm_planner.open_vision_request", return_value=Response()) as send:
            client.decide(images=[b"png"], instruction="test")
        body = json.loads(send.call_args.args[0].data)
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["max_tokens"], 4096)

    def test_extra_fields_cannot_replace_bounded_protocol_fields(self) -> None:
        for field in ("messages", "model", "max_tokens", "stream", "tools", "n"):
            with self.subTest(field=field), self.assertRaises(ContractViolation):
                OpenAICompatibleVisionClient(
                    base_url="https://example.invalid/v1", model="generic", extra_body={field: 1},
                )

    def test_image_and_reasoning_output_floors_fail_before_network(self) -> None:
        with self.assertRaises(ContractViolation):
            OpenAICompatibleVisionClient(
                base_url="https://example.invalid/v1", model="glm-5.3-flash", max_output_tokens=256,
            )
        client = OpenAICompatibleVisionClient(
            base_url="https://example.invalid/v1", model="generic"
        )
        with self.assertRaises(ContractViolation):
            client.decide(images=[b"png"] * 10, instruction="test")
