import json
from pathlib import Path

from orderflow_mm.quality import inspect_dataset, quality_json
from orderflow_mm.storage import PartitionedParquetWriter


def test_storage_round_trip_and_quality(tmp_path: Path) -> None:
    writer = PartitionedParquetWriter(tmp_path, batch_size=2)
    writer.add(
        "book_ticker",
        {
            "receive_ts_ns": 1_700_000_000_000_000_000,
            "update_id": 1,
            "symbol": "BTCUSDT",
            "bid_price": 100.0,
            "bid_qty": 2.0,
            "ask_price": 100.1,
            "ask_qty": 3.0,
        },
    )
    writer.add(
        "book_ticker",
        {
            "receive_ts_ns": 1_700_000_000_100_000_000,
            "update_id": 2,
            "symbol": "BTCUSDT",
            "bid_price": 100.1,
            "bid_qty": 1.0,
            "ask_price": 100.2,
            "ask_qty": 4.0,
        },
    )
    writer.add(
        "trade",
        {
            "receive_ts_ns": 1_700_000_000_050_000_000,
            "event_time_ms": 1_700_000_000_050,
            "trade_time_ms": 1_700_000_000_049,
            "trade_id": 9,
            "symbol": "BTCUSDT",
            "price": 100.1,
            "qty": 0.25,
            "buyer_is_maker": False,
        },
    )
    writer.flush()

    quality = inspect_dataset(tmp_path)
    assert quality["book_ticker"].rows == 2
    assert quality["book_ticker"].passed
    assert quality["trade"].rows == 1
    assert quality["trade"].passed

    historical = json.loads(quality_json(tmp_path))
    assert historical["passed"]
    assert historical["streams"]["book_ticker"]["fresh"] is None

    live = json.loads(quality_json(tmp_path, freshness_threshold_seconds=600))
    assert not live["passed"]
    assert live["streams"]["book_ticker"]["fresh"] is False
    assert live["streams"]["trade"]["fresh"] is False


def test_writer_splits_a_batch_across_utc_hours(tmp_path: Path) -> None:
    writer = PartitionedParquetWriter(tmp_path, batch_size=10)
    base = {
        "symbol": "BTCUSDT",
        "bid_price": 100.0,
        "bid_qty": 2.0,
        "ask_price": 100.1,
        "ask_qty": 3.0,
    }
    writer.add(
        "book_ticker",
        base | {"receive_ts_ns": 1_700_002_799_900_000_000, "update_id": 1},
    )
    writer.add(
        "book_ticker",
        base | {"receive_ts_ns": 1_700_002_800_100_000_000, "update_id": 2},
    )
    writer.flush()
    files = sorted(tmp_path.glob("book_ticker/date=*/hour=*/*.parquet"))
    assert len(files) == 2
    assert files[0].parent != files[1].parent
