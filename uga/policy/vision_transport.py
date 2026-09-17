"""Provider failure classification and endpoint hardening (D08).

One exception hierarchy for both the main planner and the secondary verifier:
every provider failure carries a machine-readable kind and a ``fatal`` flag so
callers can distinguish "back off and keep running" (rate limit, transient 5xx)
from "stop the run" (bad credentials, exhausted quota).

The endpoint validator enforces the network boundary: HTTPS everywhere except
loopback, no userinfo, and no credential-bearing query strings — the API key
travels in the Authorization header only.
"""

from __future__ import annotations

import re
import urllib.request
from collections.abc import Sequence
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from uga.core.errors import BackendUnavailableError

_MAX_ERROR_DETAIL_CHARS = 200

_SECRET_PATTERNS = (
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(
        r"(?i)[\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|"
        r"password|signature|authorization)[\"']?\s*[=:]\s*"
        r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}]+)"
    ),
    re.compile(r"(?i)\b(sk|ak)-[A-Za-z0-9]{8,}"),
)


class ProviderErrorKind(StrEnum):
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    QUOTA = "quota"
    TIMEOUT = "timeout"
    TRANSIENT_5XX = "transient_5xx"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    UNREACHABLE = "unreachable"
    MALFORMED_OUTPUT = "malformed_output"


class ProviderError(BackendUnavailableError):
    """A classified provider failure.

    Subclasses :class:`BackendUnavailableError` so every existing ``except
    BackendUnavailableError`` boundary keeps working; new code branches on
    ``kind`` and ``fatal`` instead of re-guessing from message strings.
    """

    def __init__(
        self,
        kind: ProviderErrorKind,
        message: str,
        *,
        fatal: bool = False,
        status: int | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.fatal = fatal
        self.status = status
        self.retry_after_s = retry_after_s


class VisionRateLimitedError(ProviderError):
    """Backward-compatible alias: the historical rate-limit exception."""

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(
            ProviderErrorKind.RATE_LIMIT,
            message,
            fatal=False,
            status=429,
            retry_after_s=retry_after_s,
        )


def redact_error_detail(
    detail: str, *, limit: int = _MAX_ERROR_DETAIL_CHARS, secrets: Sequence[str] = ()
) -> str:
    """Strip credentials from a provider error body and cap its length."""
    redacted = detail
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted[:limit]


def classify_provider_failure(status: int, detail: str) -> tuple[ProviderErrorKind, bool]:
    """Map an HTTP failure to (kind, fatal).  Fatal means "stop the run"."""
    lowered = detail.casefold()
    if status in {401, 403}:
        return ProviderErrorKind.AUTH, True
    if status == 402 or any(
        marker in lowered
        for marker in ("arrearage", "insufficient_quota", "quota_exceeded", "欠费")
    ):
        return ProviderErrorKind.QUOTA, True
    if status == 429:
        return ProviderErrorKind.RATE_LIMIT, False
    if status == 408:
        return ProviderErrorKind.TIMEOUT, False
    if status >= 500:
        return ProviderErrorKind.TRANSIENT_5XX, False
    if status in {400, 422} and any(
        token in lowered for token in ("response_format", "json_schema", "structured output")
    ):
        return ProviderErrorKind.UNSUPPORTED_CAPABILITY, False
    return ProviderErrorKind.INVALID_REQUEST, False


def validate_vision_endpoint(base_url: str) -> str:
    """Validate and normalise the chat-completions endpoint URL.

    HTTPS everywhere except loopback hosts; userinfo, credentials in the
    query string and blank hosts are refused before any request is built.
    """
    if not base_url.strip():
        raise ContractViolationError("vision endpoint requires a base URL")
    parts = urlsplit(base_url.strip())
    if not parts.hostname:
        raise ContractViolationError("vision endpoint URL has no host")
    if parts.username is not None or parts.password is not None:
        raise ContractViolationError("vision endpoint URL must not carry userinfo")
    if parts.scheme == "http":
        if parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ContractViolationError(
                "vision endpoint must use HTTPS (plain HTTP is loopback-only)"
            )
    elif parts.scheme != "https":
        raise ContractViolationError("vision endpoint scheme must be http(s)")
    if parts.query or parts.fragment:
        raise ContractViolationError("vision endpoint must not carry a query or fragment")
    try:
        _ = parts.port
    except ValueError as exc:
        raise ContractViolationError("vision endpoint port is invalid") from exc
    path = parts.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class RejectVisionRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward prompts or credentials, even to a same-host redirect.

    Configure the final endpoint explicitly. Returning None makes urllib
    raise HTTPError for redirects instead of rebuilding an authenticated GET.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def open_vision_request(request: urllib.request.Request, *, timeout: float) -> Any:
    # A private opener avoids changing process-global networking behavior.
    return urllib.request.build_opener(RejectVisionRedirect()).open(request, timeout=timeout)


class ContractViolationError(ValueError):
    """Local alias so the transport module stays independent of uga.core."""
