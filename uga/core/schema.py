from __future__ import annotations

import base64
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, ClassVar, Protocol, TypeVar, runtime_checkable

from uga.core.errors import ContractViolation

SCHEMA_VERSION = "1.1"
T = TypeVar("T", bound="VersionedSchema")


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return {"$bytes_base64": base64.b64encode(value).decode("ascii")}
    if is_dataclass(value):
        return {
            key: _json_value(item)
            for key, item in asdict(value).items()  # type: ignore[arg-type]
        }
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


@runtime_checkable
class VersionedSchema(Protocol):
    """Contract implemented by all persisted or cross-boundary records."""

    SCHEMA_NAME: ClassVar[str]
    SCHEMA_VERSION: ClassVar[str]

    def validate(self) -> None: ...

    def to_envelope(self) -> dict[str, Any]: ...


class VersionedMixin:
    SCHEMA_NAME: ClassVar[str]
    SCHEMA_VERSION: ClassVar[str] = SCHEMA_VERSION

    def validate(self) -> None:
        raise NotImplementedError

    def to_envelope(self) -> dict[str, Any]:
        self.validate()
        if not is_dataclass(self):
            raise ContractViolation("versioned schemas must be dataclasses")
        return {
            "schema": self.SCHEMA_NAME,
            "schema_version": self.SCHEMA_VERSION,
            "data": _json_value(self),
        }


def require_schema_version(envelope: dict[str, Any], schema_name: str) -> dict[str, Any]:
    if envelope.get("schema") != schema_name:
        raise ContractViolation(f"expected schema {schema_name!r}")
    if envelope.get("schema_version") != SCHEMA_VERSION:
        raise ContractViolation(f"unsupported schema version: {envelope.get('schema_version')!r}")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise ContractViolation("schema envelope data must be an object")
    return data
