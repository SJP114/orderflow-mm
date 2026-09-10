from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

NANOSECONDS_PER_SECOND = 1_000_000_000

BOOK_FEATURE_COLUMNS = [
    "mid",
    "spread",
    "book_imbalance",
    "microprice",
    "bid_price",
    "ask_price",
    "bid_qty",
    "ask_qty",
]
TRADE_FLOW_COLUMNS = ["trade_count", "buy_qty", "sell_qty", "signed_qty", "total_qty"]
TRADE_PRICE_COLUMNS = ["max_buy_price", "min_sell_price"]
MODEL_FEATURE_COLUMNS = [
    "spread_bps",
    "book_imbalance",
    "microprice_deviation_bps",
    "trade_imbalance_1s",
    "trade_imbalance_5s",
    "trade_intensity_5s",
    "log_mid_return_1s",
    "realized_volatility_10s",
]
LABEL_COLUMNS = [
    "future_mid_return_1s",
    "future_mid_return_5s",
    "future_mid_move_bps_1s",
    "future_mid_move_bps_5s",
    "future_direction_1s",
    "future_direction_5s",
]


@dataclass(frozen=True, order=True)
class HourPartition:
    date: str
    hour: int

    @property
    def start(self) -> datetime:
        return datetime.strptime(f"{self.date} {self.hour:02d}", "%Y-%m-%d %H").replace(tzinfo=UTC)

    @classmethod
    def from_datetime(cls, value: datetime) -> HourPartition:
        utc_value = value.astimezone(UTC)
        return cls(date=f"{utc_value:%Y-%m-%d}", hour=utc_value.hour)


def build_second_bars(
    book: pd.DataFrame,
    trades: pd.DataFrame,
    feature_warmup_seconds: int = 0,
) -> pd.DataFrame:
    """Build causal second-end features and explicitly future-looking labels."""
    if book.empty:
        raise ValueError("book data is required to construct mid-price bars")
    if feature_warmup_seconds < 0:
        raise ValueError("feature_warmup_seconds cannot be negative")

    book = book.sort_values(["receive_ts_ns", "update_id"], kind="stable").copy()
    book["second_ts_ns"] = book["receive_ts_ns"] // NANOSECONDS_PER_SECOND * NANOSECONDS_PER_SECOND
    book["mid"] = (book["bid_price"] + book["ask_price"]) / 2.0
    book["spread"] = book["ask_price"] - book["bid_price"]
    depth = book["bid_qty"] + book["ask_qty"]
    book["book_imbalance"] = (book["bid_qty"] - book["ask_qty"]) / depth
    book["microprice"] = (
        book["ask_price"] * book["bid_qty"] + book["bid_price"] * book["ask_qty"]
    ) / depth

    second_book = book.groupby("second_ts_ns", sort=True).agg(
        mid_open=("mid", "first"),
        mid=("mid", "last"),
        spread=("spread", "last"),
        mean_spread=("spread", "mean"),
        book_imbalance=("book_imbalance", "last"),
        mean_book_imbalance=("book_imbalance", "mean"),
        microprice=("microprice", "last"),
        bid_price=("bid_price", "last"),
        ask_price=("ask_price", "last"),
        bid_qty=("bid_qty", "last"),
        ask_qty=("ask_qty", "last"),
        book_updates=("update_id", "count"),
    )

    if trades.empty:
        second_trades = pd.DataFrame(
            columns=TRADE_FLOW_COLUMNS + TRADE_PRICE_COLUMNS + ["trade_notional", "trade_vwap"],
            index=pd.Index([], name="second_ts_ns"),
        )
    else:
        trades = trades.sort_values(["receive_ts_ns", "trade_id"], kind="stable").copy()
        trades["second_ts_ns"] = (
            trades["receive_ts_ns"] // NANOSECONDS_PER_SECOND * NANOSECONDS_PER_SECOND
        )
        trades["aggressor_sign"] = np.where(trades["buyer_is_maker"], -1.0, 1.0)
        trades["signed_qty"] = trades["qty"] * trades["aggressor_sign"]
        trades["buy_qty"] = np.where(trades["aggressor_sign"] > 0, trades["qty"], 0.0)
        trades["sell_qty"] = np.where(trades["aggressor_sign"] < 0, trades["qty"], 0.0)
        trades["aggressive_buy_price"] = trades["price"].where(trades["aggressor_sign"] > 0)
        trades["aggressive_sell_price"] = trades["price"].where(trades["aggressor_sign"] < 0)
        trades["notional"] = trades["price"] * trades["qty"]
        second_trades = trades.groupby("second_ts_ns", sort=True).agg(
            trade_count=("trade_id", "count"),
            buy_qty=("buy_qty", "sum"),
            sell_qty=("sell_qty", "sum"),
            signed_qty=("signed_qty", "sum"),
            total_qty=("qty", "sum"),
            max_buy_price=("aggressive_buy_price", "max"),
            min_sell_price=("aggressive_sell_price", "min"),
            trade_notional=("notional", "sum"),
        )
        second_trades["trade_vwap"] = second_trades["trade_notional"] / second_trades["total_qty"]

    start_second = int(min(second_book.index.min(), _optional_min(second_trades.index)))
    end_second = int(max(second_book.index.max(), _optional_max(second_trades.index)))
    full_index = pd.RangeIndex(
        start=start_second,
        stop=end_second + NANOSECONDS_PER_SECOND,
        step=NANOSECONDS_PER_SECOND,
        name="second_ts_ns",
    )
    bars = second_book.join(second_trades, how="outer").reindex(full_index)
    bars[BOOK_FEATURE_COLUMNS + ["mid_open", "mean_spread", "mean_book_imbalance"]] = bars[
        BOOK_FEATURE_COLUMNS + ["mid_open", "mean_spread", "mean_book_imbalance"]
    ].ffill()
    bars["book_updates"] = bars["book_updates"].fillna(0).astype("int64")
    observed_book_update = bars["book_updates"] > 0
    observed_seconds = pd.Series(bars.index.to_numpy(), index=bars.index, dtype="float64").where(
        observed_book_update
    )
    last_observed_second = observed_seconds.ffill()
    bars["book_data_age_seconds"] = (
        bars.index.to_numpy() - last_observed_second.to_numpy()
    ) / NANOSECONDS_PER_SECOND
    raw_data_valid = bars["book_data_age_seconds"] <= 2.0
    if feature_warmup_seconds:
        valid_run = raw_data_valid.groupby((~raw_data_valid).cumsum()).cumsum()
        bars["data_valid"] = raw_data_valid & (valid_run >= feature_warmup_seconds)
    else:
        bars["data_valid"] = raw_data_valid
    bars[TRADE_FLOW_COLUMNS + ["trade_notional"]] = bars[
        TRADE_FLOW_COLUMNS + ["trade_notional"]
    ].fillna(0.0)
    bars["trade_count"] = bars["trade_count"].astype("int64")
    bars = bars.dropna(subset=["mid"]).copy()

    bars["spread_bps"] = bars["spread"] / bars["mid"] * 10_000.0
    bars["microprice_deviation_bps"] = (bars["microprice"] - bars["mid"]) / bars["mid"] * 10_000.0
    bars["trade_imbalance_1s"] = _safe_ratio(bars["signed_qty"], bars["total_qty"])
    rolling_signed = bars["signed_qty"].rolling(5, min_periods=1).sum()
    rolling_volume = bars["total_qty"].rolling(5, min_periods=1).sum()
    bars["trade_imbalance_5s"] = _safe_ratio(rolling_signed, rolling_volume)
    bars["trade_intensity_5s"] = bars["trade_count"].rolling(5, min_periods=1).sum()
    bars["log_mid_return_1s"] = np.log(bars["mid"] / bars["mid"].shift(1)).fillna(0.0)
    bars["realized_volatility_10s"] = (
        bars["log_mid_return_1s"].rolling(10, min_periods=2).std().fillna(0.0)
    )

    for horizon in (1, 5):
        future_mid = bars["mid"].shift(-horizon)
        future_return = np.log(future_mid / bars["mid"])
        valid_window = bars["data_valid"].copy()
        for step in range(1, horizon + 1):
            valid_window &= bars["data_valid"].shift(-step, fill_value=False)
        bars[f"future_mid_return_{horizon}s"] = future_return.where(valid_window)
        bars[f"future_mid_move_bps_{horizon}s"] = (future_return * 10_000.0).where(valid_window)
        direction = np.sign(future_mid - bars["mid"])
        bars[f"future_direction_{horizon}s"] = direction.where(valid_window).astype("Int8")

    bars = bars.reset_index()
    bars["timestamp"] = pd.to_datetime(bars["second_ts_ns"], unit="ns", utc=True)
    return bars


def build_closed_hour_dataset(
    raw_root: Path,
    output_root: Path,
    now: datetime | None = None,
    replace: bool = False,
) -> list[Path]:
    """Build each closed UTC hour and refresh it when late raw parts arrive."""
    cutoff = (now or datetime.now(UTC)).astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    written: list[Path] = []
    for partition in discover_partitions(raw_root):
        if partition.start >= cutoff:
            continue
        target_dir = output_root / f"date={partition.date}" / f"hour={partition.hour:02d}"
        target_path = target_dir / "bars.parquet"
        if (
            target_path.exists()
            and not replace
            and not _raw_partition_is_newer(raw_root, partition, target_path)
        ):
            continue

        book = read_partition(raw_root / "book_ticker", partition)
        if book.empty:
            continue
        trades = read_partition(raw_root / "trade", partition)
        bars = build_second_bars(book, trades, feature_warmup_seconds=10)
        target_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = target_dir / ".bars.parquet.tmp"
        table = pa.Table.from_pandas(bars, preserve_index=False)
        pq.write_table(table, temporary_path, compression="zstd")
        os.replace(temporary_path, target_path)
        written.append(target_path)
    return written


def discover_partitions(raw_root: Path) -> list[HourPartition]:
    partitions: set[HourPartition] = set()
    for stream in ("book_ticker", "trade"):
        for hour_dir in (raw_root / stream).glob("date=*/hour=*"):
            date = hour_dir.parent.name.removeprefix("date=")
            hour = int(hour_dir.name.removeprefix("hour="))
            partitions.add(HourPartition(date=date, hour=hour))
    return sorted(partitions)


def read_partition(stream_root: Path, partition: HourPartition) -> pd.DataFrame:
    # Older writer versions could place a batch spanning an hour boundary under the
    # first row's hour. Read the preceding partition too, then filter by row timestamp.
    candidates = [HourPartition.from_datetime(partition.start - timedelta(hours=1)), partition]
    files: list[Path] = []
    for candidate in candidates:
        path = stream_root / f"date={candidate.date}" / f"hour={candidate.hour:02d}"
        files.extend(sorted(path.glob("*.parquet")))
    if not files:
        return pd.DataFrame()
    tables = [pq.ParquetFile(file).read() for file in files]
    frame = pa.concat_tables(tables).to_pandas()
    start_ns = int(partition.start.timestamp() * NANOSECONDS_PER_SECOND)
    end_ns = int((partition.start + timedelta(hours=1)).timestamp() * NANOSECONDS_PER_SECOND)
    return frame.loc[
        (frame["receive_ts_ns"] >= start_ns) & (frame["receive_ts_ns"] < end_ns)
    ].reset_index(drop=True)


def _raw_partition_is_newer(
    raw_root: Path,
    partition: HourPartition,
    target_path: Path,
) -> bool:
    dependencies = [HourPartition.from_datetime(partition.start - timedelta(hours=1)), partition]
    target_mtime_ns = target_path.stat().st_mtime_ns
    for stream in ("book_ticker", "trade"):
        for dependency in dependencies:
            directory = (
                raw_root / stream / f"date={dependency.date}" / f"hour={dependency.hour:02d}"
            )
            if any(
                path.stat().st_mtime_ns > target_mtime_ns for path in directory.glob("*.parquet")
            ):
                return True
    return False


def assert_feature_columns_are_causal(columns: list[str]) -> None:
    accidental = sorted(set(columns).intersection(LABEL_COLUMNS))
    if accidental:
        raise ValueError(f"future-looking labels cannot be model features: {accidental}")


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator.div(denominator.where(denominator != 0)).fillna(0.0)


def _optional_min(index: pd.Index) -> float:
    return float(index.min()) if len(index) else float("inf")


def _optional_max(index: pd.Index) -> float:
    return float(index.max()) if len(index) else float("-inf")
