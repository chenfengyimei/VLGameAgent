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
from enum import StrEnum
from urllib.parse import parse_qs, urlsplit

from uga.core.errors import BackendUnavailableError

_MAX_ERROR_DETAIL_CHARS = 200

_SECRET_QUERY_KEYS = ("key", "token", "secret", "signature", "apikey", "access_token")
_SECRET_PATTERNS = (
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key|token|secret|signature)\s*[=:]\s*\S+"),
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


def redact_error_detail(detail: str, *, limit: int = _MAX_ERROR_DETAIL_CHARS) -> str:
    """Strip credentials from a provider error body and cap its length."""
    redacted = detail
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted[:limit]


def classify_provider_failure(
    status: int, detail: str
) -> tuple[ProviderErrorKind, bool]:
    """Map an HTTP failure to (kind, fatal).  Fatal means "stop the run"."""
    lowered = detail.casefold()
    if status in {401, 403}:
        return ProviderErrorKind.AUTH, True
    if status == 429:
        return ProviderErrorKind.RATE_LIMIT, False
    if status == 402 or "arrearage" in lowered or "欠费" in detail:
        return ProviderErrorKind.QUOTA, True
    if status == 408:
        return ProviderErrorKind.TIMEOUT, False
    if status >= 500:
        return ProviderErrorKind.TRANSIENT_5XX, False
    if status in {400, 422} and any(
        token in lowered
        for token in ("response_format", "json_schema", "structured output")
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
    if parts.username or parts.password:
        raise ContractViolationError("vision endpoint URL must not carry userinfo")
    if parts.scheme == "http":
        if parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ContractViolationError(
                "vision endpoint must use HTTPS (plain HTTP is loopback-only)"
            )
    elif parts.scheme != "https":
        raise ContractViolationError("vision endpoint scheme must be http(s)")
    for key in parse_qs(parts.query):
        if key.casefold() in _SECRET_QUERY_KEYS:
            raise ContractViolationError(
                f"vision endpoint URL must not carry a {key!r} query parameter"
            )
    root = base_url.strip().rstrip("/")
    if not root.endswith("/chat/completions"):
        root = root + "/chat/completions"
    return root


class ContractViolationError(ValueError):
    """Local alias so the transport module stays independent of uga.core."""
