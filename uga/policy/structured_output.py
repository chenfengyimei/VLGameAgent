"""Strict consumption-side validation for model replies (D08/F11).

The declared JSON schema is a wish; the consumer-side checks here are the
law.  Booleans are never numbers, strings never coerce, NaN/Infinity never
pass, confidences are finite within [0, 1], and a bbox must live in ONE
coordinate space — a frame that mixes unit fractions with 0-1000 pixels is
rejected instead of silently rescaled (EX07).
"""

from __future__ import annotations

import math
from typing import Any

_BBOX_LENGTH = 4


def strict_finite_number(value: Any) -> float:
    """A JSON number (never ``bool``, never ``str``) that must be finite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"expected a number, got {type(value).__name__}")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError("number exceeds finite range") from exc
    if not math.isfinite(number):
        raise ValueError("number must be finite (NaN/Infinity rejected)")
    return number


def strict_unit_interval_number(value: Any) -> float:
    """A finite number within [0, 1] — confidences and normalized coordinates."""
    number = strict_finite_number(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"number must be within [0, 1], got {number}")
    return number


def strict_confidence_value(value: Any) -> float | None:
    """``None`` when absent; raises when present but not a finite [0, 1] number."""
    if value is None:
        return None
    return strict_unit_interval_number(value)


def strict_coordinates(value: Any) -> tuple[float, float, float, float]:
    """A four-number bbox in ONE consistent space: all [0,1] or all (1,1000].

    EX07 regression: a frame that mixes unit fractions with 0-1000 pixels is
    rejected — the old "any value > 1 rescales the whole frame" guess turned
    malformed output into a plausible-looking box.
    """
    if not isinstance(value, list) or len(value) != _BBOX_LENGTH:
        raise ValueError("target_bbox must contain exactly four numbers")
    raw = tuple(strict_finite_number(item) for item in value)
    in_unit = all(0.0 <= number <= 1.0 for number in raw)
    in_thousand = all(1.0 < number <= 1000.0 for number in raw)
    if in_unit:
        left, top, right, bottom = raw
        return (left, top, right, bottom)
    if in_thousand:
        left, top, right, bottom = (number / 1000.0 for number in raw)
        return (left, top, right, bottom)
    raise ValueError(
        "target_bbox mixes coordinate spaces (unit [0,1] and 0-1000 pixels)"
    )


def strict_bounded_text(value: Any, *, max_chars: int, field: str) -> str:
    """A plain string with a hard length cap (overlong model rambling rejected)."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if len(value) > max_chars:
        raise ValueError(f"{field} exceeds {max_chars} characters")
    return value
