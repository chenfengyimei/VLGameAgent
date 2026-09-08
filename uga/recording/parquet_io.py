from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from uga.core.errors import BackendUnavailableError


def _arrow() -> tuple[Any, Any]:
    try:
        pa = importlib.import_module("pyarrow")
        pq = importlib.import_module("pyarrow.parquet")
    except ImportError as exc:
        raise BackendUnavailableError(
            "Parquet recording requires the declared pyarrow dependency"
        ) from exc
    return pa, pq


ColumnSpec = tuple[str, str]


def write_rows(path: Path, rows: list[dict[str, Any]], schema: tuple[ColumnSpec, ...]) -> None:
    pa, pq = _arrow()
    types = {
        "string": pa.string(),
        "int64": pa.int64(),
        "float64": pa.float64(),
        "bool": pa.bool_(),
    }
    arrays = {
        name: pa.array([row.get(name) for row in rows], type=types[type_name])
        for name, type_name in schema
    }
    table = pa.table(arrays)
    pq.write_table(table, path, compression="zstd")


def read_rows(path: Path) -> list[dict[str, Any]]:
    _, pq = _arrow()
    return list(pq.read_table(path).to_pylist())
