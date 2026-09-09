from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

from uga.core.artifact_limits import ArtifactResourceLimits
from uga.core.errors import ContractViolation
from uga.recording.parquet_io import read_rows, write_rows


class ParquetDecodedByteLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.path = self.root / "probe.parquet"
        # 2,000 rows of 1,000-byte strings: ~2 MB decoded, but dictionary
        # encoded by pyarrow down to a few kilobytes on disk, so on-disk and
        # declared metadata stay far below any configured byte cap.
        self.rows = [{"seq": index, "payload": "A" * 1000} for index in range(2000)]
        write_rows(self.path, self.rows, (("seq", "int64"), ("payload", "string")))
        self.limits = ArtifactResourceLimits(
            max_parquet_file_bytes=2 * 1024 * 1024,
            max_parquet_uncompressed_bytes=64 * 1024,
            max_parquet_rows=10_000,
            max_parquet_columns=64,
        )

    def test_decoded_size_beyond_the_limit_fails_closed(self) -> None:
        # The declared row-group metadata stays tiny because the column is
        # dictionary encoded, so only actual decoded accounting can catch it.
        with self.assertRaises(ContractViolation):
            read_rows(self.path, limits=self.limits)

    def test_declared_metadata_beyond_the_limit_fails_closed(self) -> None:
        generous = ArtifactResourceLimits(
            max_parquet_file_bytes=2 * 1024 * 1024,
            max_parquet_uncompressed_bytes=1,
            max_parquet_rows=1000,
            max_parquet_columns=64,
        )
        with self.assertRaises(ContractViolation):
            read_rows(self.path, limits=generous)

    def test_row_and_column_limits_still_apply(self) -> None:
        for field, value in (("max_parquet_rows", 1), ("max_parquet_columns", 1)):
            limits = dataclasses.replace(self.limits, **{field: value})
            with self.assertRaises(ContractViolation):
                read_rows(self.path, limits=limits)

    def test_small_episode_roundtrip_still_reads(self) -> None:
        small = self.root / "small.parquet"
        rows = [{"seq": index, "payload": "x"} for index in range(16)]
        write_rows(small, rows, (("seq", "int64"), ("payload", "string")))
        self.assertEqual(read_rows(small), rows)


if __name__ == "__main__":
    unittest.main()
