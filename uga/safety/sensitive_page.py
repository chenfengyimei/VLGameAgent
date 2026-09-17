"""Runtime-owned sensitive-page policy; model labels/goals grant no authority.

Recognized payments, credentials, legal consent and destructive confirmations
are handled by the owner. OCR detection is conservative, not a privacy guarantee.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from uga.perception.builder import normalize_visible_text
from uga.perception.schema import ActionRisk, GroundedAction, TextRegion

_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("identity", ("\u5b9e\u540d", "\u8eab\u4efd\u8bc1", "\u9632\u6c89\u8ff7", "passportnumber")),
    (
        "credentials",
        (
            "\u8bf7\u8f93\u5165\u5bc6\u7801",
            "\u5bc6\u7801",
            "\u9a8c\u8bc1\u7801",
            "password",
            "verificationcode",
            "one-timecode",
            "recoveryphrase",
            "privatekey",
        ),
    ),
    (
        "payment",
        (
            "\u786e\u8ba4\u652f\u4ed8",
            "\u7acb\u5373\u652f\u4ed8",
            "\u94f6\u884c\u5361",
            "\u652f\u4ed8\u5b9d",
            "\u5fae\u4fe1\u652f\u4ed8",
            "checkout",
            "creditcard",
            "billingaddress",
            "confirm payment",
            "buy now",
        ),
    ),
    (
        "consent",
        (
            "\u540c\u610f\u7528\u6237\u534f\u8bae",
            "\u540c\u610f\u534f\u8bae",
            "\u9690\u79c1\u653f\u7b56",
            "\u670d\u52a1\u6761\u6b3e",
            "acceptterms",
            "agreetoterms",
            "terms of service",
            "privacy policy",
        ),
    ),
    (
        "destructive",
        (
            "\u786e\u8ba4\u5220\u9664",
            "\u6c38\u4e45\u5220\u9664",
            "\u6ce8\u9500\u8d26\u53f7",
            "permanentlydelete",
            "deleteaccount",
            "confirmdeletion",
            "erasealldata",
        ),
    ),
)
_DESTRUCTIVE_TARGETS = (
    "delete",
    "erase",
    "pay",
    "purchase",
    "\u5220\u9664",
    "\u652f\u4ed8",
    "\u4ed8\u6b3e",
    "\u5145\u503c",
    "\u540c\u610f\u534f\u8bae",
    "acceptterms",
)


@dataclass(frozen=True, slots=True)
class SensitivePageVerdict:
    requires_owner: bool
    category: str | None = None

    @property
    def reason(self) -> str:
        return (
            f"owner_confirmation_required:{self.category}"
            if self.requires_owner
            else "ordinary_page"
        )


def inspect_sensitive_page(
    visible_text: Sequence[TextRegion],
    action: GroundedAction | None = None,
) -> SensitivePageVerdict:
    text = "".join(normalize_visible_text(item.text) for item in visible_text)
    for category, cues in _CUES:
        if any(normalize_visible_text(cue) in text for cue in cues):
            return SensitivePageVerdict(True, category)
    if action is not None:
        label = normalize_visible_text(action.target_label)
        if action.risk == ActionRisk.CRITICAL or any(
            normalize_visible_text(cue) in label for cue in _DESTRUCTIVE_TARGETS
        ):
            return SensitivePageVerdict(True, "critical_action")
    return SensitivePageVerdict(False)
