from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from uga.control.lifetime import ActionLifetime
from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin


class SemanticActionKind(StrEnum):
    NAVIGATE_TO_TARGET = "navigate_to_target"
    INTERACT_WITH_TARGET = "interact_with_target"
    FOLLOW_TARGET = "follow_target"
    ESCAPE_THREAT = "escape_threat"
    OPEN_INVENTORY = "open_inventory"
    BUY_ITEM = "buy_item"
    EQUIP_ITEM = "equip_item"


@dataclass(frozen=True, slots=True)
class SemanticAction(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.semantic_action"

    action_id: str
    kind: SemanticActionKind
    target: str | None
    parameters: tuple[tuple[str, str], ...]
    lifetime: ActionLifetime

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.action_id.strip():
            raise ContractViolation("semantic action id cannot be empty")
        if self.target is not None and not self.target.strip():
            raise ContractViolation("semantic action target cannot be blank")
        keys = [key for key, _ in self.parameters]
        if any(not key.strip() for key in keys) or len(keys) != len(set(keys)):
            raise ContractViolation("semantic action parameter keys must be unique and non-empty")
        self.lifetime.validate()
