"""Version-specific request constraints, not a live provider qualification.

Reference verified 2026-09-17: https://help.aliyun.com/en/model-studio/glm-zhipu
GLM-5.3 family cannot disable thinking; reasoning_effort is low/high/max.
"""

from __future__ import annotations

from typing import Any

from uga.core.errors import ContractViolation

_RESERVED = frozenset({"model", "messages", "max_tokens", "stream", "response_format"})


def model_request_options(
    model: str,
    *,
    disable_thinking: bool,
    extra_body: dict[str, Any] | None,
) -> dict[str, Any]:
    extra = dict(extra_body or {})
    if _RESERVED.intersection(extra):
        raise ContractViolation(
            "extra_body cannot override model, input, output budget or protocol"
        )
    name = model.casefold().rsplit("/", 1)[-1]
    if name in {"glm-5.3", "glm-5.3-flash"}:
        thinking = extra.get("thinking", {"type": "enabled"})
        if (
            disable_thinking
            or extra.get("enable_thinking") is False
            or not isinstance(thinking, dict)
            or thinking.get("type") != "enabled"
        ):
            raise ContractViolation("GLM-5.3 models require thinking.type=enabled")
        effort = extra.get("reasoning_effort", "max")
        if not isinstance(effort, str) or effort not in {"low", "high", "max"}:
            raise ContractViolation("GLM-5.3 reasoning_effort must be low, high or max")
        extra["thinking"] = {"type": "enabled"}
    elif "qwen3-vl" in name:
        # Instruct and Thinking are different checkpoints, not a GLM-style switch.
        # Backend-specific template extensions must be explicitly configured.
        if "thinking" in name and disable_thinking:
            raise ContractViolation("use a Qwen3-VL Instruct checkpoint for no-thinking mode")
        if "thinking" in extra:
            raise ContractViolation("Qwen3-VL does not use the GLM thinking request field")
    elif disable_thinking:
        if "thinking" in extra or extra.get("enable_thinking") is True:
            raise ContractViolation("conflicting thinking switches")
        extra["thinking"] = {"type": "disabled"}
    return extra
