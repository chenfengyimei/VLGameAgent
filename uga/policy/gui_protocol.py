"""Bounded, consumption-side GUI reply and coordinate contracts.

The selected output space is explicit: never infer it from the magnitude of
one coordinate. OCR/runtime boxes remain unit coordinates at every boundary.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from uga.policy.structured_output import strict_finite_number

COORDINATE_SPACES = ("unit", "normalized_1000")
MAX_REPLY_CHARS = 16_384


def reply_object(reply: str, *, allow_answer_wrapper: bool = True) -> dict[str, Any]:
    if not isinstance(reply, str) or len(reply) > MAX_REPLY_CHARS:
        raise ValueError("GUI reply exceeds the text budget")
    text = reply.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) < 3 or lines[0].strip() not in {"```", "```json"} or lines[-1] != "```":
            raise ValueError("GUI reply must contain one JSON object")
        text = "\n".join(lines[1:-1])

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate GUI JSON field")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError("nonfinite JSON number: " + value)

    try:
        data = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("GUI reply must contain one JSON object") from exc
    if not isinstance(data, dict):
        raise ValueError("GUI reply must be an object")
    if "answer" in data:
        if (
            not allow_answer_wrapper
            or set(data) != {"answer"}
            or not isinstance(data["answer"], str)
        ):
            raise ValueError("ambiguous GUI answer wrapper")
        return reply_object(data["answer"], allow_answer_wrapper=False)
    return data


def require_fields(
    data: dict[str, Any], fields: set[str], *, optional: set[str] | None = None
) -> None:
    if fields - data.keys() or data.keys() - fields - (optional or set()):
        raise ValueError("GUI object has missing or unexpected fields")


def box_coordinates(value: Any, space: str) -> tuple[float, float, float, float]:
    if space not in COORDINATE_SPACES:
        raise ValueError("unsupported GUI coordinate space")
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("target_bbox must contain exactly four numbers")
    raw = tuple(strict_finite_number(number) for number in value)
    upper = 1.0 if space == "unit" else 1000.0
    if not all(0 <= number <= upper for number in raw):
        raise ValueError("target_bbox outside the declared coordinate space")
    # The thousand-space contract uses integer grid coordinates, so accidental
    # unit fractions cannot be mistaken for a tiny but valid target.
    if space == "normalized_1000" and any(number != int(number) for number in raw):
        raise ValueError("normalized_1000 coordinates must be integers")
    left, top, right, bottom = raw
    return left / upper, top / upper, right / upper, bottom / upper


def coordinate_format(format_: dict[str, Any], space: str) -> dict[str, Any]:
    if space not in COORDINATE_SPACES:
        raise ValueError("unsupported GUI coordinate space")
    result = copy.deepcopy(format_)
    if space == "normalized_1000":
        action = result["json_schema"]["schema"]["properties"]["action"]["anyOf"][1]
        for key in ("target_bbox", "end_bbox"):
            if key in action["properties"]:
                items = action["properties"][key]["anyOf"][1]["items"]
                items.update(type="integer", maximum=1000)
    return result
