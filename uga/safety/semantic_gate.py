"""Conservative operator handoff, independent of model labels and rule sources.

OCR/explicit-risk checks are not a universal classifier of icon-only sensitive
interfaces. Neither a broad user goal nor model confidence grants consent.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from uga.perception.schema import ActionRisk, GroundedAction, TextRegion


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


_PAGE_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "payment",
        (
            "confirm payment",
            "payment password",
            "credit card",
            "checkout",
            "buy now",
            "\u786e\u8ba4\u652f\u4ed8",
            "\u652f\u4ed8\u5bc6\u7801",
            "\u7acb\u5373\u8d2d\u4e70",
            "\u786e\u8ba4\u5145\u503c",
            "\u786e\u8ba4\u4ed8\u6b3e",
            "\u652f\u4ed8\u91d1\u989d",
        ),
    ),
    (
        "agreement",
        (
            "terms of service",
            "privacy policy",
            "accept agreement",
            "agree to the terms",
            "\u540c\u610f\u7528\u6237\u534f\u8bae",
            "\u9690\u79c1\u653f\u7b56",
            "\u7528\u6237\u534f\u8bae",
        ),
    ),
    (
        "credentials",
        (
            "password",
            "verification code",
            "one-time code",
            "sign in",
            "log in",
            "\u8f93\u5165\u5bc6\u7801",
            "\u9a8c\u8bc1\u7801",
            "\u8d26\u53f7\u767b\u5f55",
        ),
    ),
    (
        "deletion",
        (
            "permanently delete",
            "delete account",
            "confirm deletion",
            "delete character",
            "\u786e\u8ba4\u5220\u9664",
            "\u5220\u9664\u89d2\u8272",
            "\u6c38\u4e45\u5220\u9664",
            "\u6ce8\u9500\u8d26\u53f7",
        ),
    ),
)


def sensitive_page_reason(regions: Sequence[TextRegion]) -> str | None:
    # Unrelated text regions must not be concatenated into invented evidence.
    for region in regions:
        if region.confidence < 0.5:
            continue
        text = _normal(region.text)
        for category, cues in _PAGE_CUES:
            if any(cue in text for cue in cues):
                return f"sensitive {category} page requires operator handoff"
    return None


def sensitive_action_reason(action: GroundedAction | None) -> str | None:
    if action is None:
        return None
    if action.risk == ActionRisk.CRITICAL:
        return "critical action requires operator handoff"
    label = _normal(action.target_label)
    terms = (
        "pay",
        "purchase",
        "buy",
        "delete",
        "install",
        "send",
        "login",
        "sign in",
        "\u652f\u4ed8",
        "\u4ed8\u6b3e",
        "\u5145\u503c",
        "\u5220\u9664",
        "\u5b89\u88c5",
        "\u767b\u5f55",
        "\u540c\u610f\u534f\u8bae",
        "\u540c\u610f\u7528\u6237\u534f\u8bae",
    )
    for term in terms:
        if term.isascii():
            if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", label):
                return "sensitive action requires operator handoff"
        elif term in label:
            return "sensitive action requires operator handoff"
    return None
