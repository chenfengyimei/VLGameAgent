"""Repository request policy, not a claim of live provider qualification.

The GLM-5.3-Flash entry implements the handoff's enabled-thinking contract.
Availability, official current limits and live accuracy require external evidence.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from uga.core.errors import ContractViolation

_RESERVED = frozenset({
    "model", "messages", "max_tokens", "max_completion_tokens", "response_format",
    "stream", "tools", "tool_choice", "n", "stop",
})


@dataclass(frozen=True, slots=True)
class ModelRequestPolicy:
    thinking_required: bool = False
    minimum_output_budget: int = 64
    max_images: int = 9
    live_qualification: str = "NOT_RUN"


def request_policy(model: str) -> ModelRequestPolicy:
    if model.casefold() == "glm-5.3-flash":
        # This output floor is our operational policy, not an API limit.
        return ModelRequestPolicy(thinking_required=True, minimum_output_budget=1024)
    return ModelRequestPolicy()


def validated_extensions(
    model: str, *, disable_thinking: bool, max_output_tokens: int,
    extra_body: dict[str, Any] | None,
) -> dict[str, Any]:
    extra = copy.deepcopy(extra_body) if extra_body else {}
    if _RESERVED.intersection(extra):
        raise ContractViolation("extra body cannot replace bounded protocol or safety fields")
    policy = request_policy(model)
    if max_output_tokens < policy.minimum_output_budget:
        raise ContractViolation("model output budget is below the repository request policy floor")
    if policy.thinking_required:
        if (disable_thinking or extra.get("enable_thinking") is False
                or ("thinking" in extra and extra["thinking"] != {"type": "enabled"})):
            raise ContractViolation("this model request policy requires enabled thinking")
        extra["thinking"] = {"type": "enabled"}
    return extra
