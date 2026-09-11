from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from uga.core.errors import ContractViolation


class EnvironmentClass(StrEnum):
    SINGLE_PLAYER = "single_player"
    OFFLINE = "offline"
    PRIVATE_TEST_SERVER = "private_test_server"
    DEVELOPER_OWNED = "developer_owned"
    OPEN_SOURCE = "open_source"
    RESEARCH_SANDBOX = "research_sandbox"
    ONLINE = "online"
    COMPETITIVE = "competitive"
    UNKNOWN = "unknown"


class PolicyReason(StrEnum):
    ALLOWED = "allowed"
    AUTOMATION_DISABLED = "automation_disabled"
    MULTIPLAYER = "multiplayer"
    ANTI_CHEAT = "anti_cheat"
    ENVIRONMENT_NOT_ALLOWED = "environment_not_allowed"


@dataclass(frozen=True, slots=True)
class EnvironmentSafetyManifest:
    environment_class: EnvironmentClass
    automation_allowed: bool
    multiplayer: bool
    anti_cheat_present: bool

    def __post_init__(self) -> None:
        if any(
            type(value) is not bool
            for value in (self.automation_allowed, self.multiplayer, self.anti_cheat_present)
        ):
            raise ContractViolation("environment safety flags must be booleans")


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: PolicyReason


_ALLOWED_CLASSES = frozenset(
    {
        EnvironmentClass.SINGLE_PLAYER,
        EnvironmentClass.OFFLINE,
        EnvironmentClass.PRIVATE_TEST_SERVER,
        EnvironmentClass.DEVELOPER_OWNED,
        EnvironmentClass.OPEN_SOURCE,
        EnvironmentClass.RESEARCH_SANDBOX,
    }
)


def evaluate_environment(manifest: EnvironmentSafetyManifest) -> PolicyDecision:
    """Fail closed unless the environment is explicitly safe for automation."""
    if not manifest.automation_allowed:
        return PolicyDecision(False, PolicyReason.AUTOMATION_DISABLED)
    if manifest.anti_cheat_present:
        return PolicyDecision(False, PolicyReason.ANTI_CHEAT)
    if manifest.multiplayer:
        return PolicyDecision(False, PolicyReason.MULTIPLAYER)
    if manifest.environment_class not in _ALLOWED_CLASSES:
        return PolicyDecision(False, PolicyReason.ENVIRONMENT_NOT_ALLOWED)
    return PolicyDecision(True, PolicyReason.ALLOWED)


def require_safe_environment(manifest: EnvironmentSafetyManifest) -> None:
    decision = evaluate_environment(manifest)
    if not decision.allowed:
        raise ContractViolation(f"environment safety policy rejected runtime: {decision.reason}")
