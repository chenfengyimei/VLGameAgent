from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import ClassVar

from uga.core.errors import ContractViolation
from uga.core.schema import VersionedMixin


class TaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    ACTIVE = "active"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_ns: int = 0

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.backoff_ns < 0:
            raise ContractViolation("invalid task retry policy")


@dataclass(frozen=True, slots=True)
class TaskNode(VersionedMixin):
    SCHEMA_NAME: ClassVar[str] = "uga.task_node"

    node_id: str
    instruction: str
    parent_id: str | None
    child_ids: tuple[str, ...]
    status: TaskStatus
    prerequisite_ids: tuple[str, ...]
    selected_skill: str | None
    success_condition: str
    failure_condition: str
    timeout_ns: int
    retry_policy: RetryPolicy
    attempts: int = 0

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.node_id.strip() or not self.instruction.strip():
            raise ContractViolation("task node requires id and instruction")
        if not self.success_condition.strip() or not self.failure_condition.strip():
            raise ContractViolation("task node requires success and failure conditions")
        if self.timeout_ns <= 0 or self.attempts < 0:
            raise ContractViolation("task timeout/attempt count is invalid")
        if self.node_id in self.child_ids or self.node_id in self.prerequisite_ids:
            raise ContractViolation("task node cannot depend on itself")


class TaskGraph:
    def __init__(self, nodes: tuple[TaskNode, ...]) -> None:
        self._nodes = {node.node_id: node for node in nodes}
        if len(self._nodes) != len(nodes) or not nodes:
            raise ContractViolation("task graph requires unique nodes")
        self._validate_references()
        self._validate_acyclic()
        self.refresh_ready()

    def get(self, node_id: str) -> TaskNode:
        return self._nodes[node_id]

    def nodes(self) -> tuple[TaskNode, ...]:
        return tuple(self._nodes.values())

    def refresh_ready(self) -> None:
        for node_id, node in tuple(self._nodes.items()):
            if node.status != TaskStatus.PENDING:
                continue
            prerequisite_states = [self._nodes[item].status for item in node.prerequisite_ids]
            if any(
                state in (TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED)
                for state in prerequisite_states
            ):
                self._nodes[node_id] = replace(node, status=TaskStatus.BLOCKED)
            elif all(state == TaskStatus.SUCCESS for state in prerequisite_states):
                self._nodes[node_id] = replace(node, status=TaskStatus.READY)

    def activate(self, node_id: str) -> TaskNode:
        node = self._nodes[node_id]
        if node.status != TaskStatus.READY:
            raise ContractViolation(f"only ready tasks can activate, got {node.status}")
        if node.attempts >= node.retry_policy.max_attempts:
            raise ContractViolation("task retry policy is exhausted")
        updated = replace(node, status=TaskStatus.ACTIVE, attempts=node.attempts + 1)
        self._nodes[node_id] = updated
        return updated

    def complete(self, node_id: str, *, success: bool) -> TaskNode:
        node = self._nodes[node_id]
        if node.status != TaskStatus.ACTIVE:
            raise ContractViolation("only active tasks can complete")
        updated = replace(node, status=TaskStatus.SUCCESS if success else TaskStatus.FAILED)
        self._nodes[node_id] = updated
        self.refresh_ready()
        return updated

    def retry(self, node_id: str) -> TaskNode:
        node = self._nodes[node_id]
        if node.status != TaskStatus.FAILED or node.attempts >= node.retry_policy.max_attempts:
            raise ContractViolation("task is not retryable")
        updated = replace(node, status=TaskStatus.READY)
        self._nodes[node_id] = updated
        return updated

    def _validate_references(self) -> None:
        for node in self._nodes.values():
            references = (*node.child_ids, *node.prerequisite_ids)
            if any(reference not in self._nodes for reference in references):
                raise ContractViolation(f"task {node.node_id!r} references a missing node")
            if node.parent_id is not None and node.parent_id not in self._nodes:
                raise ContractViolation(f"task {node.node_id!r} has a missing parent")

    def _validate_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()
        adjacency = {node_id: set(node.child_ids) for node_id, node in self._nodes.items()}
        for node in self._nodes.values():
            for prerequisite_id in node.prerequisite_ids:
                adjacency[prerequisite_id].add(node.node_id)

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ContractViolation("task graph contains a cycle")
            if node_id in visited:
                return
            visiting.add(node_id)
            for child_id in adjacency[node_id]:
                visit(child_id)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in self._nodes:
            visit(node_id)
