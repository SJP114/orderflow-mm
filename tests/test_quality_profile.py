from pathlib import Path

from orderflow_mm.quality import hourly_volume_profile
from orderflow_mm.storage import PartitionedParquetWriter


def test_hourly_profile_marks_complete_and_partial_hours(tmp_path: Path) -> None:
    writer = PartitionedParquetWriter(tmp_path, batch_size=100)
    hour_start_ns = 1_700_002_800_000_000_000
    base = {
        "symbol": "BTCUSDT",
        "bid_price": 100.0,
        "bid_qty": 2.0,
        "ask_price": 100.1,
        "ask_qty": 3.0,
    }
    writer.add(
        "book_ticker",
        base | {"receive_ts_ns": hour_start_ns + 10_000_000_000, "update_id": 1},
    )
    writer.add(
        "book_ticker",
        base | {"receive_ts_ns": hour_start_ns + 3_590_000_000_000, "update_id": 2},
    )
    writer.flush()
    profile = hourly_volume_profile(tmp_path)
    book = profile.loc[profile["stream"] == "book_ticker"].iloc[0]
    assert book["rows"] == 2
    assert bool(book["is_full_hour"])
