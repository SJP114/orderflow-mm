from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orderflow_mm.storage import PartitionedParquetWriter


def generate_sample_data(output_root: Path, seconds: int = 180) -> dict[str, Any]:
    """Write a small deterministic raw-data fixture through the production writer."""
    if seconds < 20:
        raise ValueError("seconds must be at least 20 so rolling features can warm up")
    if any(output_root.rglob("*.parquet")):
        raise FileExistsError(f"sample output already contains Parquet files: {output_root}")

    start_ns = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    writer = PartitionedParquetWriter(output_root, batch_size=seconds + 1)
    for offset in range(seconds):
        receive_ts_ns = start_ns + offset * 1_000_000_000 + 100_000_000
        mid = 40_000.0 + 0.25 * offset + 2.0 * math.sin(offset / 7.0)
        bid_price = round(mid - 0.05, 2)
        ask_price = round(mid + 0.05, 2)
        writer.add(
            "book_ticker",
            {
                "receive_ts_ns": receive_ts_ns,
                "update_id": offset + 1,
                "symbol": "BTCUSDT",
                "bid_price": bid_price,
                "bid_qty": 1.0 + (offset % 5) * 0.1,
                "ask_price": ask_price,
                "ask_qty": 1.4 - (offset % 4) * 0.1,
            },
        )
        buyer_is_maker = offset % 2 == 0
        writer.add(
            "trade",
            {
                "receive_ts_ns": receive_ts_ns + 200_000_000,
                "event_time_ms": (receive_ts_ns + 200_000_000) // 1_000_000,
                "trade_time_ms": (receive_ts_ns + 200_000_000) // 1_000_000,
                "trade_id": 10_000 + offset,
                "symbol": "BTCUSDT",
                "price": bid_price if buyer_is_maker else ask_price,
                "qty": 0.001 + (offset % 3) * 0.0002,
                "buyer_is_maker": buyer_is_maker,
            },
        )

    writer.flush()
    return {
        "output": str(output_root),
        "seconds": seconds,
        "book_ticker_rows": seconds,
        "trade_rows": seconds,
        "files_written": writer.files_written,
        "research_only": True,
    }
