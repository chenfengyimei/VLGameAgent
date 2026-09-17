"""Strict bounded training values; never silently coerce untrusted JSON."""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from uga.core.errors import ContractViolation


def finite_number(value: object, name: str, *, maximum: float = 1e12) -> float:
    if type(value) not in (int, float):
        raise ContractViolation(f"{name} must be a finite number, not a boolean or string")
    try:
        result = float(value)  # type: ignore[arg-type]
    except (OverflowError, ValueError) as exc:
        raise ContractViolation(f"{name} is outside the numeric range") from exc
    if not math.isfinite(result) or abs(result) > maximum:
        raise ContractViolation(f"{name} is outside the finite numeric range")
    return result


def integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ContractViolation(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ContractViolation(f"{name} must be a nonblank bounded string")
    return value


def strict_json(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ContractViolation(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ContractViolation(f"non-finite JSON value: {value}")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except ContractViolation:
        raise
    except (ValueError, RecursionError) as exc:
        raise ContractViolation("invalid training JSON") from exc


def atomic_text(destination: Path, text: str) -> Path:
    """Atomically replace one derived file; callers first exclude all inputs."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".uga-output-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)
    return destination
