from __future__ import annotations

import os
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from orderflow_mm.schema import SCHEMAS


class PartitionedParquetWriter:
    """Buffer rows and atomically publish immutable Parquet parts."""

    def __init__(self, root: Path, batch_size: int = 10_000) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.root = root
        self.batch_size = batch_size
        self.buffers: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.files_written = 0
        self.rows_written = 0

    def add(self, stream_name: str, row: dict[str, Any]) -> None:
        if stream_name not in SCHEMAS:
            raise ValueError(f"unsupported stream: {stream_name}")
        self.buffers[stream_name].append(row)
        if len(self.buffers[stream_name]) >= self.batch_size:
            self.flush(stream_name)

    def flush(self, stream_name: str | None = None) -> None:
        names = [stream_name] if stream_name else list(self.buffers)
        for name in names:
            rows = self.buffers.get(name, [])
            if not rows:
                continue
            partitioned_rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                receive_time = datetime.fromtimestamp(
                    int(row["receive_ts_ns"]) / 1_000_000_000, tz=UTC
                )
                partitioned_rows[(f"{receive_time:%Y-%m-%d}", f"{receive_time:%H}")].append(row)
            for partition_rows in partitioned_rows.values():
                self._write_part(name, partition_rows)
            rows.clear()

    def _write_part(self, stream_name: str, rows: list[dict[str, Any]]) -> None:
        first_ts = int(rows[0]["receive_ts_ns"])
        dt = datetime.fromtimestamp(first_ts / 1_000_000_000, tz=UTC)
        partition = self.root / stream_name / f"date={dt:%Y-%m-%d}" / f"hour={dt:%H}"
        partition.mkdir(parents=True, exist_ok=True)

        token = uuid.uuid4().hex[:12]
        filename = f"part-{time.time_ns()}-{token}.parquet"
        final_path = partition / filename
        temporary_path = partition / f".{filename}.tmp"

        table = pa.Table.from_pylist(rows, schema=SCHEMAS[stream_name])
        pq.write_table(table, temporary_path, compression="zstd")
        os.replace(temporary_path, final_path)
        self.files_written += 1
        self.rows_written += len(rows)
