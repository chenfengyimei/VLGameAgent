"""Game-strategy isolation (D12): explicit, per-profile rule inventories.

A rule inventory declares which decision fast paths a profile may use —
name, decision source, page precondition, allowed action, risk and effect.
The inventory is DATA: it grants nothing by itself and can never bypass the
unified action safety gate (D03); it only scopes which rule code the planner
is allowed to consult for a given game.

A generic profile resolves to an EMPTY registry, so none of the commercial-
game fast paths (task-panel clicks, dialog cancels, market/stall rules…)
ever fire for it.  ``None`` as the planner's registry is a documented legacy
compat default (unit tests) meaning "allow every known source".
"""

from __future__ import annotations

from dataclasses import dataclass

from uga.core.errors import ContractViolation


@dataclass(frozen=True, slots=True)
class StrategyRule:
    """One game-scoped decision rule, declared as reviewable data."""

    name: str
    source: str
    game_id: str
    summary: str
    allowed_action: str
    risk: str
    effect: str
    cooldown_s: float = 0.0


@dataclass(frozen=True, slots=True)
class StrategyRegistry:
    """The rule inventory of exactly one game profile."""

    game_id: str
    rules: tuple[StrategyRule, ...]

    def __post_init__(self) -> None:
        if not self.game_id.strip():
            raise ContractViolation("strategy registry requires a game id")
        _require_distinct_sources(self.rules)
        for rule in self.rules:
            if rule.game_id != self.game_id:
                raise ContractViolation(
                    f"rule {rule.name!r} does not belong to game {self.game_id!r}"
                )

    def rule_for(self, source: str) -> StrategyRule | None:
        for rule in self.rules:
            if rule.source == source:
                return rule
        return None

    def allows(self, source: str) -> bool:
        return self.rule_for(source) is not None

    def allows_fast_paths(self) -> bool:
        return bool(self.rules)


def _require_distinct_sources(rules: tuple[StrategyRule, ...]) -> None:
    seen: set[str] = set()
    for rule in rules:
        if rule.source in seen:
            raise ContractViolation(f"duplicate strategy source: {rule.source}")
        seen.add(rule.source)


def registry_for(game_id: str) -> StrategyRegistry:
    """The rule inventory of ``game_id`` — EMPTY for unregistered games.

    A generic profile therefore never consults any game rule; the planner
    treats an empty registry as "no game fast paths".
    """
    if not game_id.strip():
        raise ContractViolation("strategy registry requires a game id")
    if game_id == "mumu-xianyu":
        # Lazy import: the inventory module registers itself with this package.
        from uga.agent.strategies.mumu_xianyu import REGISTRY

        return REGISTRY
    return StrategyRegistry(game_id=game_id, rules=())
