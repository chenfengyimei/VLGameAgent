from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from uga.core.artifact_limits import DEFAULT_ARTIFACT_LIMITS, ArtifactResourceLimits
from uga.core.errors import BackendUnavailableError, ContractViolation


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


def read_rows(
    path: Path,
    *,
    limits: ArtifactResourceLimits = DEFAULT_ARTIFACT_LIMITS,
) -> list[dict[str, Any]]:
    _, pq = _arrow()
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(0)
            if size > limits.max_parquet_file_bytes:
                raise ContractViolation("Parquet file exceeds the compressed byte limit")
            parquet = pq.ParquetFile(stream)
            metadata = parquet.metadata
            if metadata.num_rows > limits.max_parquet_rows:
                raise ContractViolation("Parquet file exceeds the row limit")
            if metadata.num_columns > limits.max_parquet_columns:
                raise ContractViolation("Parquet file exceeds the column limit")
            uncompressed = sum(
                metadata.row_group(index).total_byte_size
                for index in range(metadata.num_row_groups)
            )
            if uncompressed > limits.max_parquet_uncompressed_bytes:
                raise ContractViolation("Parquet file exceeds the uncompressed byte limit")
            return list(parquet.read().to_pylist())
    except OSError as exc:
        raise ContractViolation(f"cannot read Parquet file: {path}") from exc
