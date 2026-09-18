"""Credential and output boundary tests; no real keys and no network calls."""

from __future__ import annotations

import argparse
import io
import json
import unittest
import urllib.request
from unittest.mock import MagicMock, patch

from apps.agent.__main__ import _run_safely
from tests.helpers import frame
from tests.unit.test_closed_loop_supervisor import outcome, snapshot
from uga.policy.grounded_vlm import GroundedOutcomeVerifier
from uga.policy.vision_transport import (
    ProviderError,
    ProviderErrorKind,
    RejectVisionRedirect,
    classify_provider_failure,
    redact_error_detail,
    validate_vision_endpoint,
)
from uga.policy.vlm_planner import OpenAICompatibleVisionClient


class ReviewTransportTests(unittest.TestCase):
    def test_json_and_quoted_credentials_are_redacted(self) -> None:
        for body in (
            '{"api_key": "fake-credential-value"}',
            "{'access_token': 'fake-credential-value'}",
            '{"nested":{"Authorization":"Bearer fake-credential-value"}}',
        ):
            self.assertNotIn("fake-credential-value", redact_error_detail(body))
        self.assertNotIn(
            "custom-value",
            redact_error_detail(
                "Echoed custom-value",
                secrets=("custom-value",),
            ),
        )

    def test_authenticated_redirect_is_refused(self) -> None:
        request = urllib.request.Request(
            "https://provider.test/v1/chat/completions",
            data=b"private pixels",
            headers={"Authorization": "Bearer test-only"},
        )
        for destination in ("https://other.test/", "http://provider.test/", request.full_url):
            self.assertIsNone(
                RejectVisionRedirect().redirect_request(
                    request,
                    None,
                    302,
                    "redirect",
                    {},
                    destination,
                )
            )

    def test_query_and_fragment_cannot_change_endpoint_path(self) -> None:
        for suffix in ("?region=test", "#fragment", "?api_key="):
            with self.assertRaises(ValueError):
                validate_vision_endpoint("https://provider.test/v1" + suffix)

    def test_quota_encoded_as_429_is_fatal(self) -> None:
        self.assertEqual(
            classify_provider_failure(429, "insufficient_quota"), (ProviderErrorKind.QUOTA, True)
        )

    def test_reasoning_text_is_not_executable_content(self) -> None:
        client = OpenAICompatibleVisionClient(base_url="https://provider.test/v1", model="test")
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": '{"kind":"act"}',
                        }
                    }
                ]
            }
        ).encode()
        with (
            patch("uga.policy.vlm_planner.open_vision_request", return_value=response),
            self.assertRaises(ProviderError),
        ):
            client.decide(images=[b"test"], instruction="test")

    def test_verifier_fatal_auth_is_not_an_ordinary_rejection(self) -> None:
        client = MagicMock()
        client.decide.side_effect = ProviderError(
            ProviderErrorKind.AUTH,
            "bad credentials",
            fatal=True,
        )
        verifier = GroundedOutcomeVerifier(client)
        with self.assertRaises(ProviderError):
            verifier(outcome(1), snapshot(1, 0), frame(1, 0), "settings")

    def test_verifier_non_object_json_is_rejected_without_crashing(self) -> None:
        client = MagicMock()
        client.decide.return_value = "[]"
        self.assertFalse(
            GroundedOutcomeVerifier(client)(
                outcome(1),
                snapshot(1, 0),
                frame(1, 0),
                "settings",
            )
        )


class ProviderExitTests(unittest.TestCase):
    def test_task_group_auth_failure_has_non_retryable_exit_code(self) -> None:
        error = ExceptionGroup(
            "agent task failed",
            [
                ProviderError(
                    ProviderErrorKind.AUTH,
                    "provider body is not console output",
                    fatal=True,
                )
            ],
        )
        stderr = io.StringIO()
        with (
            patch("apps.agent.__main__._run", side_effect=error),
            patch("sys.stderr", stderr),
        ):
            self.assertEqual(_run_safely(argparse.Namespace()), 78)
        self.assertNotIn("provider body", stderr.getvalue())

    def test_retired_timeout_worker_requests_process_restart(self) -> None:
        error = ExceptionGroup(
            "agent task failed",
            [
                ProviderError(
                    ProviderErrorKind.TIMEOUT,
                    "decision worker retired",
                    fatal=True,
                )
            ],
        )
        stderr = io.StringIO()
        with (
            patch("apps.agent.__main__._run", side_effect=error),
            patch("sys.stderr", stderr),
        ):
            self.assertEqual(_run_safely(argparse.Namespace()), 1)
        self.assertIn("restartable", stderr.getvalue())

    def test_unrelated_failures_are_not_swallowed(self) -> None:
        with (
            patch("apps.agent.__main__._run", side_effect=RuntimeError("unexpected")),
            self.assertRaisesRegex(RuntimeError, "unexpected"),
        ):
            _run_safely(argparse.Namespace())
