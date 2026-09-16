"""D08 regression tests: provider failure classification, endpoint hardening
and strict consumption-side validation (F10/F11/R19, EX05/EX07)."""

from __future__ import annotations

import json
import unittest
import urllib.error
from unittest.mock import patch

from uga.policy.grounded_vlm import GroundedVlmPlanner, PlannerReplyError
from uga.policy.structured_output import (
    strict_bounded_text,
    strict_confidence_value,
    strict_coordinates,
    strict_finite_number,
    strict_unit_interval_number,
)
from uga.policy.vision_transport import (
    ProviderError,
    ProviderErrorKind,
    VisionRateLimitedError,
    classify_provider_failure,
    redact_error_detail,
    validate_vision_endpoint,
)


class EndpointValidationTests(unittest.TestCase):
    def test_https_endpoint_is_normalised(self) -> None:
        self.assertEqual(
            validate_vision_endpoint("https://open.bigmodel.cn/api/paas/v4"),
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        )

    def test_plain_http_is_loopback_only(self) -> None:
        self.assertEqual(
            validate_vision_endpoint("http://127.0.0.1:1234/v1"),
            "http://127.0.0.1:1234/v1/chat/completions",
        )
        with self.assertRaises(ValueError):
            validate_vision_endpoint("http://api.example.com/v1")

    def test_userinfo_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            validate_vision_endpoint("https://user:secret@api.example.com/v1")

    def test_credential_query_parameters_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            validate_vision_endpoint("https://api.example.com/v1?key=abc123")

    def test_blank_host_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            validate_vision_endpoint("   ")


class ClassificationTests(unittest.TestCase):
    def test_auth_failures_are_fatal(self) -> None:
        for status in (401, 403):
            kind, fatal = classify_provider_failure(status, "bad credentials")
            self.assertEqual(kind, ProviderErrorKind.AUTH)
            self.assertTrue(fatal)

    def test_rate_limit_is_not_fatal(self) -> None:
        kind, fatal = classify_provider_failure(429, "slow down")
        self.assertEqual(kind, ProviderErrorKind.RATE_LIMIT)
        self.assertFalse(fatal)

    def test_quota_is_fatal(self) -> None:
        kind, fatal = classify_provider_failure(402, "Arrearage")
        self.assertEqual(kind, ProviderErrorKind.QUOTA)
        self.assertTrue(fatal)
        kind, fatal = classify_provider_failure(400, "账户已欠费")
        self.assertEqual(kind, ProviderErrorKind.QUOTA)
        self.assertTrue(fatal)

    def test_transient_5xx_is_not_fatal(self) -> None:
        kind, fatal = classify_provider_failure(503, "overloaded")
        self.assertEqual(kind, ProviderErrorKind.TRANSIENT_5XX)
        self.assertFalse(fatal)

    def test_redaction_strips_credentials(self) -> None:
        detail = "401 Unauthorized: Bearer abc123def; api_key=xyz987; sk-abcdefgh12345678"
        redacted = redact_error_detail(detail)
        self.assertNotIn("abc123def", redacted)
        self.assertNotIn("xyz987", redacted)
        self.assertNotIn("abcdefgh12345678", redacted)
        self.assertIn("[redacted]", redacted)

    def test_redaction_caps_length(self) -> None:
        self.assertLessEqual(len(redact_error_detail("x" * 5000)), 200)


class StrictNumberTests(unittest.TestCase):
    def test_bool_is_never_a_number(self) -> None:
        with self.assertRaises(ValueError):
            strict_finite_number(True)
        with self.assertRaises(ValueError):
            strict_unit_interval_number(False)

    def test_string_never_coerces(self) -> None:
        with self.assertRaises(ValueError):
            strict_finite_number("0.9")

    def test_nan_and_infinity_are_rejected(self) -> None:
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                strict_finite_number(bad)

    def test_confidence_out_of_range_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            strict_unit_interval_number(1.5)
        with self.assertRaises(ValueError):
            strict_unit_interval_number(-0.1)

    def test_confidence_none_means_absent(self) -> None:
        self.assertIsNone(strict_confidence_value(None))
        self.assertEqual(strict_confidence_value(0.9), 0.9)
        with self.assertRaises(ValueError):
            strict_confidence_value(True)


class StrictCoordinateTests(unittest.TestCase):
    def test_unit_frame_accepted(self) -> None:
        self.assertEqual(
            strict_coordinates([0.1, 0.2, 0.4, 0.5]), (0.1, 0.2, 0.4, 0.5)
        )

    def test_thousand_scale_frame_accepted(self) -> None:
        self.assertEqual(
            strict_coordinates([100, 200, 400, 500]),
            (0.1, 0.2, 0.4, 0.5),
        )

    def test_mixed_spaces_rejected(self) -> None:
        # EX07 regression: the old rescale turned mixed frames into a
        # plausible-looking box instead of rejecting them.
        with self.assertRaises(ValueError):
            strict_coordinates([0.2, 300, 0.4, 500])

    def test_wrong_length_and_non_numbers_rejected(self) -> None:
        with self.assertRaises(ValueError):
            strict_coordinates([0.1, 0.2, 0.3])
        with self.assertRaises(ValueError):
            strict_coordinates(["0.1", "0.2", "0.3", "0.4"])
        with self.assertRaises(ValueError):
            strict_coordinates([True, 0.2, 0.3, 0.4])

    def test_bounded_text_caps_length(self) -> None:
        self.assertEqual(strict_bounded_text("ok", max_chars=10, field="f"), "ok")
        with self.assertRaises(ValueError):
            strict_bounded_text("x" * 11, max_chars=10, field="f")


class ProviderFatalPropagationTests(unittest.TestCase):
    def test_auth_provider_error_is_fatal_and_backwards_compatible(self) -> None:
        error = ProviderError(
            ProviderErrorKind.AUTH, "invalid credentials", fatal=True, status=401
        )
        self.assertTrue(error.fatal)
        # Backward compatibility: every legacy except-boundary still catches.
        self.assertIsInstance(error, BackendUnavailableErrorParent)


from uga.core.errors import BackendUnavailableError as BackendUnavailableErrorParent  # noqa: E402


class ClientFailureClassificationTests(unittest.TestCase):
    """The real client maps HTTP failures onto the unified classification."""

    class _FakeHTTPError(urllib.error.HTTPError):
        def __init__(self, status: int, body: bytes) -> None:
            super().__init__(
                url="https://unit.test/chat/completions",
                code=status,
                msg="error",
                hdrs={"Content-Type": "application/json"},
                fp=None,  # type: ignore[arg-type]
            )
            self._body = body

        def read(self, amount: int = -1) -> bytes:  # type: ignore[override]
            return self._body

    def _client(self):
        from uga.policy.vlm_planner import OpenAICompatibleVisionClient

        return OpenAICompatibleVisionClient(
            base_url="https://unit.test/v1",
            model="unit-model",
            api_key="unit-key",
        )

    def test_401_maps_to_fatal_auth(self) -> None:
        client = self._client()
        payload = json.dumps({"error": {"message": "invalid api key"}}).encode()
        with patch(
            "uga.policy.vlm_planner.urllib.request.urlopen",
            side_effect=self._FakeHTTPError(401, payload),
        ), self.assertRaises(ProviderError) as caught:
            client.decide(images=[b"png"], instruction="go")
        self.assertEqual(caught.exception.kind, ProviderErrorKind.AUTH)
        self.assertTrue(caught.exception.fatal)
        # Backward compatibility: legacy boundaries still catch it.
        self.assertIsInstance(caught.exception, BackendUnavailableErrorParent)

    def test_429_maps_to_rate_limit_with_retry_after(self) -> None:
        client = self._client()
        payload = json.dumps({"error": {"message": "slow down"}}).encode()
        error = self._FakeHTTPError(429, payload)
        error.headers = {"Content-Type": "application/json", "Retry-After": "7"}
        with patch(
            "uga.policy.vlm_planner.urllib.request.urlopen",
            side_effect=error,
        ), self.assertRaises(VisionRateLimitedError) as caught:
            client.decide(images=[b"png"], instruction="go")
        self.assertEqual(caught.exception.retry_after_s, 7.0)
        self.assertFalse(caught.exception.fatal)


import urllib.error  # noqa: E402


class OutcomeParsingStrictnessTests(unittest.TestCase):
    """EX05/F11: malformed model fields are rejected, never coerced."""

    def _planner(self) -> GroundedVlmPlanner:
        from tests.unit.test_grounded_vlm import _snapshot

        planner = GroundedVlmPlanner.__new__(GroundedVlmPlanner)
        del _snapshot
        return planner

    def test_bool_confidence_is_rejected(self) -> None:
        from tests.unit.test_grounded_vlm import _snapshot

        planner = GroundedVlmPlanner.__new__(GroundedVlmPlanner)
        reply = json.dumps(
            {
                "kind": "wait",
                "confidence": True,
                "wait_reason": "loading",
                "action": None,
            }
        )
        with self.assertRaises(PlannerReplyError):
            planner._parse(reply, _snapshot())

    def test_nan_confidence_is_rejected(self) -> None:
        from tests.unit.test_grounded_vlm import _snapshot

        planner = GroundedVlmPlanner.__new__(GroundedVlmPlanner)
        reply = '{"kind": "act", "confidence": NaN, "action": null}'
        with self.assertRaises(PlannerReplyError):
            planner._parse(reply, _snapshot())

    def test_mixed_coordinate_bbox_is_rejected(self) -> None:
        from tests.unit.test_grounded_vlm import _snapshot

        planner = GroundedVlmPlanner.__new__(GroundedVlmPlanner)
        reply = json.dumps(
            {
                "kind": "act",
                "confidence": 0.9,
                "action": {
                    "kind": "click",
                    "target_label": "x",
                    "target_bbox": [0.2, 300, 0.4, 500],
                    "expected_effect": "e",
                    "confidence": 0.9,
                    "risk": "low",
                    "key": None,
                },
            }
        )
        with self.assertRaises(PlannerReplyError):
            planner._parse(reply, _snapshot())


if __name__ == "__main__":
    unittest.main()
