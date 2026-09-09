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

# Bounded read batches keep decoded-byte accounting ahead of materialization.
_READ_BATCH_ROWS = 4096


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
            declared = sum(
                metadata.row_group(index).total_byte_size
                for index in range(metadata.num_row_groups)
            )
            # Declared row-group sizes reflect encoded on-disk bytes and can
            # understate decoded data arbitrarily (dictionary encoding), so they
            # act only as an early filter before actual decoded-byte accounting.
            if declared > limits.max_parquet_uncompressed_bytes:
                raise ContractViolation("Parquet file exceeds the uncompressed byte limit")
            rows: list[dict[str, Any]] = []
            decoded_bytes = 0
            for batch in parquet.iter_batches(batch_size=_READ_BATCH_ROWS):
                decoded_bytes += batch.nbytes
                if decoded_bytes > limits.max_parquet_uncompressed_bytes:
                    raise ContractViolation("Parquet file exceeds the uncompressed byte limit")
                rows.extend(batch.to_pylist())
            return rows
    except OSError as exc:
        raise ContractViolation(f"cannot read Parquet file: {path}") from exc
