import os
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from orderflow_mm.bars import (
    MODEL_FEATURE_COLUMNS,
    assert_feature_columns_are_causal,
    build_closed_hour_dataset,
    build_second_bars,
)
from orderflow_mm.storage import PartitionedParquetWriter


def _book_row(second: int, offset_ns: int, update_id: int, bid: float, ask: float) -> dict:
    return {
        "receive_ts_ns": second * 1_000_000_000 + offset_ns,
        "update_id": update_id,
        "symbol": "BTCUSDT",
        "bid_price": bid,
        "bid_qty": 3.0,
        "ask_price": ask,
        "ask_qty": 1.0,
    }


def test_second_bars_use_last_observation_and_future_labels() -> None:
    book = pd.DataFrame(
        [
            _book_row(1, 100, 1, 99.0, 101.0),
            _book_row(1, 900, 2, 100.0, 102.0),
            _book_row(2, 100, 3, 101.0, 103.0),
            _book_row(3, 100, 4, 101.0, 103.0),
            _book_row(4, 100, 5, 102.0, 104.0),
            _book_row(5, 100, 6, 102.0, 104.0),
            _book_row(6, 100, 7, 103.0, 105.0),
        ]
    )
    trades = pd.DataFrame(
        [
            {
                "receive_ts_ns": 1_000_000_500,
                "event_time_ms": 1000,
                "trade_time_ms": 1000,
                "trade_id": 7,
                "symbol": "BTCUSDT",
                "price": 101.0,
                "qty": 2.0,
                "buyer_is_maker": False,
            },
            {
                "receive_ts_ns": 1_000_000_600,
                "event_time_ms": 1000,
                "trade_time_ms": 1000,
                "trade_id": 8,
                "symbol": "BTCUSDT",
                "price": 100.0,
                "qty": 1.0,
                "buyer_is_maker": True,
            },
        ]
    )

    bars = build_second_bars(book, trades).set_index("second_ts_ns")
    first = bars.loc[1_000_000_000]
    assert first["mid"] == 101.0
    assert first["book_updates"] == 2
    assert first["trade_count"] == 2
    assert first["signed_qty"] == 1.0
    assert first["trade_imbalance_1s"] == pytest.approx(1.0 / 3.0)
    assert first["future_mid_move_bps_1s"] > 0
    assert first["future_direction_5s"] == 1


def test_feature_prefix_is_unchanged_when_future_data_is_added() -> None:
    prefix_book = pd.DataFrame(
        [
            _book_row(1, 100, 1, 99.0, 101.0),
            _book_row(2, 100, 2, 100.0, 102.0),
        ]
    )
    extended_book = pd.concat(
        [prefix_book, pd.DataFrame([_book_row(3, 100, 3, 110.0, 112.0)])],
        ignore_index=True,
    )
    empty_trades = pd.DataFrame()

    prefix = build_second_bars(prefix_book, empty_trades)
    extended = build_second_bars(extended_book, empty_trades)
    pd.testing.assert_frame_equal(
        prefix[MODEL_FEATURE_COLUMNS],
        extended.iloc[: len(prefix)][MODEL_FEATURE_COLUMNS].reset_index(drop=True),
    )


def test_model_features_reject_future_labels() -> None:
    assert_feature_columns_are_causal(MODEL_FEATURE_COLUMNS)
    with pytest.raises(ValueError, match="future-looking"):
        assert_feature_columns_are_causal(MODEL_FEATURE_COLUMNS + ["future_mid_return_1s"])


def test_stale_book_seconds_are_invalid_and_do_not_receive_labels() -> None:
    book = pd.DataFrame(
        [
            _book_row(1, 100, 1, 99.0, 101.0),
            _book_row(6, 100, 2, 100.0, 102.0),
        ]
    )
    bars = build_second_bars(book, pd.DataFrame()).set_index("second_ts_ns")
    assert bars.loc[3_000_000_000, "data_valid"]
    assert not bars.loc[4_000_000_000, "data_valid"]
    assert pd.isna(bars.loc[3_000_000_000, "future_direction_1s"])


def test_feature_warmup_excludes_first_seconds_after_a_gap() -> None:
    book = pd.DataFrame([_book_row(second, 100, second, 99.0, 101.0) for second in range(1, 13)])
    bars = build_second_bars(book, pd.DataFrame(), feature_warmup_seconds=3).set_index(
        "second_ts_ns"
    )
    assert not bars.loc[1_000_000_000, "data_valid"]
    assert not bars.loc[2_000_000_000, "data_valid"]
    assert bars.loc[3_000_000_000, "data_valid"]


def test_closed_hour_is_rebuilt_when_late_raw_part_arrives(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    output_root = tmp_path / "processed"
    writer = PartitionedParquetWriter(raw_root, batch_size=10)
    hour_start = datetime(2026, 1, 1, tzinfo=UTC)
    start_second = int(hour_start.timestamp())
    writer.add("book_ticker", _book_row(start_second + 1, 100, 1, 99.0, 101.0))
    writer.add("book_ticker", _book_row(start_second + 2, 100, 2, 100.0, 102.0))
    writer.flush()

    written = build_closed_hour_dataset(
        raw_root,
        output_root,
        now=hour_start.replace(hour=1),
    )
    assert len(written) == 1
    target = written[0]
    assert pq.ParquetFile(target).metadata.num_rows == 2

    writer.add("book_ticker", _book_row(start_second + 3, 100, 3, 101.0, 103.0))
    writer.flush()
    late_part = max(
        raw_root.glob("book_ticker/date=*/hour=*/*.parquet"), key=lambda p: p.stat().st_mtime_ns
    )
    newer_ns = target.stat().st_mtime_ns + 1_000_000_000
    os.utime(late_part, ns=(newer_ns, newer_ns))

    refreshed = build_closed_hour_dataset(
        raw_root,
        output_root,
        now=hour_start.replace(hour=1),
    )
    assert refreshed == [target]
    assert pq.ParquetFile(target).metadata.num_rows == 3
