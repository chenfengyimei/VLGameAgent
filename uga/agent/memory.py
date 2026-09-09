from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from uga.core.artifact_limits import parse_json_text
from uga.core.errors import ContractViolation
from uga.recording.json_codec import canonical_json
from uga.time.clock import ClockBackend, UGATime


class MemoryKind(StrEnum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    kind: MemoryKind
    key: str
    content: object
    tags: tuple[str, ...]
    source: str
    created_at: UGATime


class MemoryStore:
    """Local SQLite memory with JSON payloads and no vector-store dependency."""

    def __init__(self, path: str | Path, clock: ClockBackend) -> None:
        self._path = Path(path)
        self._clock = clock
        self._connection = sqlite3.connect(self._path, check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                key TEXT NOT NULL,
                content_json TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at_ns INTEGER NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_lookup ON memories(kind, key, created_at_ns)"
        )
        self._connection.commit()

    def put(
        self,
        kind: MemoryKind,
        key: str,
        content: object,
        *,
        tags: tuple[str, ...] = (),
        source: str,
    ) -> MemoryRecord:
        if not key.strip() or not source.strip() or any(not tag.strip() for tag in tags):
            raise ContractViolation("memory key, source, and tags cannot be blank")
        record = MemoryRecord(uuid.uuid4().hex, kind, key, content, tags, source, self._clock.now())
        self._connection.execute(
            "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.memory_id,
                record.kind.value,
                record.key,
                canonical_json(record.content),
                canonical_json(record.tags),
                record.source,
                record.created_at.value_ns,
            ),
        )
        self._connection.commit()
        return record

    def query(
        self, *, kind: MemoryKind | None = None, key: str | None = None, limit: int = 20
    ) -> tuple[MemoryRecord, ...]:
        if limit < 1:
            raise ContractViolation("memory query limit must be positive")
        clauses: list[str] = []
        values: list[object] = []
        if kind is not None:
            clauses.append("kind = ?")
            values.append(kind.value)
        if key is not None:
            clauses.append("key = ?")
            values.append(key)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._connection.execute(
            "SELECT memory_id, kind, key, content_json, tags_json, source, created_at_ns "
            f"FROM memories{where} ORDER BY created_at_ns DESC, memory_id DESC LIMIT ?",
            (*values, limit),
        ).fetchall()
        return tuple(
            MemoryRecord(
                str(row[0]),
                MemoryKind(str(row[1])),
                str(row[2]),
                parse_json_text(str(row[3])),
                tuple(str(item) for item in parse_json_text(str(row[4]))),
                str(row[5]),
                UGATime(int(row[6])),
            )
            for row in rows
        )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
