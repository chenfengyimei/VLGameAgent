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
from functools import partial
from pathlib import Path
from typing import Any

from uga.recording.channel import RecorderChannel


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
        max_file_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        if capacity < 1:
            raise ValueError("decision journal capacity must be positive")
        if max_file_bytes < 1024:
            raise ValueError("journal file budget must be at least 1024 bytes")
        self._max_file_bytes = max_file_bytes
        self._disk: RecorderChannel | None = None
        self._disk_error: str | None = None
        self._closed = False
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
        # Bound optional diagnostics; the durable sink receives original records.
        row = {
            key: value[:8192] if isinstance(value, str) else value
            for key, value in record.to_row().items()
        }
        row["wall_clock"] = time.strftime("%H:%M:%S", time.localtime(record.timestamp))
        with self._lock:
            if self._closed:
                raise RuntimeError("decision journal is closed")
            self._ring.append(row)
            kind = (
                record.kind
                if record.kind in self._counters or len(self._counters) < 64
                else "other"
            )
            self._counters[kind] = self._counters.get(kind, 0) + 1
            if record.latency_s is not None and record.kind == "decision":
                self._latencies.append(record.latency_s)
            if self._path is not None and self._disk_error is None:
                try:
                    encoded = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
                    if len(encoded) > self._max_file_bytes:
                        raise ValueError("diagnostic row exceeds file budget")
                    if self._disk is None:
                        self._disk = RecorderChannel(128, max_pending_bytes=2 * 1024 * 1024)
                    self._disk.submit(
                        partial(self._append_disk, encoded),
                        timeout_s=0,
                        weight_bytes=len(encoded),
                    )
                except Exception as exc:
                    # Optional diagnostics never block control or copy raw OS
                    # exception messages which may include sensitive material.
                    self._disk_error = type(exc).__name__
            sink = self._sink
        # Episode persistence is part of the run evidence contract, so unlike
        # the optional dashboard JSONL file its failures must reach the caller.
        # Invoke outside the journal lock to avoid lock-order coupling with the
        # recorder's own synchronization.
        if sink is not None:
            sink(record)

    def _append_disk(self, encoded: bytes) -> None:
        path = self._path
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size + len(encoded) > self._max_file_bytes:
            path.replace(path.with_name(path.name + ".1"))
        with path.open("ab") as handle:
            handle.write(encoded)

    def close(self, timeout_s: float = 1.0) -> None:
        """A blocked optional log device cannot trap shutdown."""
        with self._lock:
            self._closed = True
            disk = self._disk
        if disk is not None:
            try:
                disk.close(timeout_s)
            except Exception as exc:
                with self._lock:
                    self._disk_error = type(exc).__name__

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            ring = list(self._ring)
            counters = dict(self._counters)
            latencies = sorted(self._latencies)
            disk = self._disk
            disk_error = self._disk_error
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
        return {
            "stats": stats,
            "events": ring[-120:],
            "diagnostic_file_error": disk_error,
            "diagnostic_queue": {} if disk is None else disk.stats(),
        }


class NullJournal(DecisionJournal):
    """No-op journal for tests and runs without a dashboard."""

    def __init__(self) -> None:
        super().__init__(path=None, capacity=1)

    def record(self, record: DecisionRecord) -> None:  # noqa: D102 - no-op
        del record

    def snapshot(self) -> dict[str, Any]:  # noqa: D102 - no-op
        return {"stats": {}, "events": []}
