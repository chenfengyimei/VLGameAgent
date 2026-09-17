"""Real-time decision journal for the vision planner.

Every planner decision (success, failure, skip, queued replay) is appended
to an in-memory ring plus an optional JSONL file so the run dashboard can
show what the model decided, how long it took, and why.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One planner decision event (success, failure, skip, or replay)."""

    timestamp: float
    kind: str  # decision | queued | static_hold | stale_discard | failure | rate_limited
    latency_s: float | None
    action: str | None
    detail: str | None
    quest: str | None
    quest_step: str | None
    images: int | None
    reply_head: str | None

    def to_row(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "kind": self.kind,
            "latency_s": self.latency_s,
            "action": self.action,
            "detail": self.detail,
            "quest": self.quest,
            "quest_step": self.quest_step,
            "images": self.images,
            "reply_head": self.reply_head,
        }


class DecisionJournal:
    """Thread-safe decision log with an in-memory ring and JSONL sink."""

    def __init__(
        self,
        path: Path | None = None,
        capacity: int = 512,
        *,
        sink: Callable[[DecisionRecord], None] | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("decision journal capacity must be positive")
        self._path = path
        self._lock = threading.Lock()
        self._ring: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._counters: dict[str, int] = {}
        self._latencies: deque[float] = deque(maxlen=64)
        self._sink = sink

    def set_sink(self, sink: Callable[[DecisionRecord], None] | None) -> None:
        """Attach the durable run sink after its Episode writer is ready."""
        with self._lock:
            self._sink = sink

    def record(self, record: DecisionRecord) -> None:
        row = record.to_row()
        row["wall_clock"] = time.strftime("%H:%M:%S", time.localtime(record.timestamp))
        with self._lock:
            self._ring.append(row)
            self._counters[record.kind] = self._counters.get(record.kind, 0) + 1
            if record.latency_s is not None and record.kind == "decision":
                self._latencies.append(record.latency_s)
            if self._path is not None:
                try:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    with open(self._path, "a", encoding="utf-8") as handle:
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                except OSError:
                    pass  # the dashboard is best-effort; never kill the run
            sink = self._sink
        # Episode persistence is part of the run evidence contract, so unlike
        # the optional dashboard JSONL file its failures must reach the caller.
        # Invoke outside the journal lock to avoid lock-order coupling with the
        # recorder's own synchronization.
        if sink is not None:
            sink(record)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            ring = list(self._ring)
            counters = dict(self._counters)
            latencies = sorted(self._latencies)
        stats = {
            "total": sum(counters.values()),
            "by_kind": counters,
            "decisions": sum(1 for row in ring if row.get("kind") == "decision"),
            "static_holds": sum(1 for row in ring if row.get("kind") == "static_hold"),
            "queued_replays": sum(1 for row in ring if row.get("kind") == "queued"),
        }
        if latencies:
            stats["latency_avg_s"] = sum(latencies) / len(latencies)
            stats["latency_p50_s"] = latencies[len(latencies) // 2]
            stats["latency_max_s"] = latencies[-1]
        return {"stats": stats, "events": ring[-120:]}


class NullJournal(DecisionJournal):
    """No-op journal for tests and runs without a dashboard."""

    def __init__(self) -> None:
        super().__init__(path=None, capacity=1)

    def record(self, record: DecisionRecord) -> None:  # noqa: D102 - no-op
        del record

    def snapshot(self) -> dict[str, Any]:  # noqa: D102 - no-op
        return {"stats": {}, "events": []}
