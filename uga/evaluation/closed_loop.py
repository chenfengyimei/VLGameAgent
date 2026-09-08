from __future__ import annotations

from dataclasses import dataclass

from uga.core.errors import ContractViolation


@dataclass(frozen=True, slots=True)
class ClosedLoopEpisodeResult:
    episode_id: str
    success: bool
    progress: float
    latency_ms: float
    expired_actions: int
    total_actions: int
    camera_jerk: float


@dataclass(frozen=True, slots=True)
class ClosedLoopMetrics:
    episodes: int
    success_rate: float
    mean_progress: float
    mean_latency_ms: float
    expired_action_rate: float
    mean_camera_jerk: float


class ClosedLoopEvaluator:
    def summarize(self, results: tuple[ClosedLoopEpisodeResult, ...]) -> ClosedLoopMetrics:
        if not results:
            raise ContractViolation("closed-loop evaluation requires episode results")
        total_actions = sum(item.total_actions for item in results)
        if total_actions < 1:
            raise ContractViolation("closed-loop evaluation requires executed/proposed actions")
        count = len(results)
        return ClosedLoopMetrics(
            count,
            sum(item.success for item in results) / count,
            sum(item.progress for item in results) / count,
            sum(item.latency_ms for item in results) / count,
            sum(item.expired_actions for item in results) / total_actions,
            sum(item.camera_jerk for item in results) / count,
        )
