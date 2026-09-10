from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds


@dataclass
class StreamQuality:
    stream: str
    files: int
    rows: int
    min_receive_ts_ns: int | None
    max_receive_ts_ns: int | None
    duplicate_event_ids: int
    nonpositive_prices: int
    nonpositive_quantities: int
    max_receive_gap_ms: float
    gaps_over_5s: int
    crossed_quotes: int = 0
    locked_quotes: int = 0

    @property
    def passed(self) -> bool:
        return (
            self.rows > 0
            and self.duplicate_event_ids == 0
            and self.nonpositive_prices == 0
            and self.nonpositive_quantities == 0
            and self.crossed_quotes == 0
        )


def inspect_dataset(root: Path) -> dict[str, StreamQuality]:
    results: dict[str, StreamQuality] = {}
    for stream in ("book_ticker", "trade"):
        stream_root = root / stream
        files = sorted(stream_root.glob("date=*/hour=*/*.parquet"))
        if not files:
            results[stream] = StreamQuality(
                stream=stream,
                files=0,
                rows=0,
                min_receive_ts_ns=None,
                max_receive_ts_ns=None,
                duplicate_event_ids=0,
                nonpositive_prices=0,
                nonpositive_quantities=0,
                max_receive_gap_ms=0.0,
                gaps_over_5s=0,
            )
            continue

        dataset = ds.dataset([str(path) for path in files], format="parquet")
        columns = (
            ["receive_ts_ns", "update_id", "bid_price", "bid_qty", "ask_price", "ask_qty"]
            if stream == "book_ticker"
            else ["receive_ts_ns", "trade_id", "price", "qty"]
        )
        table = dataset.to_table(columns=columns)
        identifiers = table["update_id"] if stream == "book_ticker" else table["trade_id"]
        distinct_ids = pc.count_distinct(identifiers).as_py()
        receive_times = table["receive_ts_ns"]
        sorted_receive_times = np.sort(
            receive_times.combine_chunks().to_numpy(zero_copy_only=False)
        )
        receive_gaps = np.diff(sorted_receive_times)
        max_receive_gap_ms = float(receive_gaps.max() / 1_000_000.0) if len(receive_gaps) else 0.0
        gaps_over_5s = int(np.count_nonzero(receive_gaps > 5_000_000_000))

        if stream == "book_ticker":
            prices = pa.concat_arrays(
                [table["bid_price"].combine_chunks(), table["ask_price"].combine_chunks()]
            )
            quantities = pa.concat_arrays(
                [table["bid_qty"].combine_chunks(), table["ask_qty"].combine_chunks()]
            )
            crossed = pc.sum(
                pc.cast(pc.greater(table["bid_price"], table["ask_price"]), "int64")
            ).as_py()
            locked = pc.sum(
                pc.cast(pc.equal(table["bid_price"], table["ask_price"]), "int64")
            ).as_py()
        else:
            prices = table["price"]
            quantities = table["qty"]
            crossed = 0
            locked = 0

        results[stream] = StreamQuality(
            stream=stream,
            files=len(files),
            rows=table.num_rows,
            min_receive_ts_ns=pc.min(receive_times).as_py(),
            max_receive_ts_ns=pc.max(receive_times).as_py(),
            duplicate_event_ids=table.num_rows - int(distinct_ids),
            nonpositive_prices=pc.sum(pc.cast(pc.less_equal(prices, 0), "int64")).as_py(),
            nonpositive_quantities=pc.sum(pc.cast(pc.less_equal(quantities, 0), "int64")).as_py(),
            max_receive_gap_ms=max_receive_gap_ms,
            gaps_over_5s=gaps_over_5s,
            crossed_quotes=int(crossed),
            locked_quotes=int(locked),
        )
    return results


def quality_json(root: Path, freshness_threshold_seconds: float | None = None) -> str:
    snapshot_started_at_ns = time.time_ns()
    results = inspect_dataset(root)
    streams: dict[str, dict[str, object]] = {}
    for name, result in results.items():
        lag_seconds = (
            max(0.0, (snapshot_started_at_ns - result.max_receive_ts_ns) / 1_000_000_000)
            if result.max_receive_ts_ns is not None
            else None
        )
        fresh = (
            lag_seconds is not None and lag_seconds <= freshness_threshold_seconds
            if freshness_threshold_seconds is not None
            else None
        )
        streams[name] = asdict(result) | {
            "passed": result.passed,
            "freshness_lag_seconds": lag_seconds,
            "fresh": fresh,
        }
    payload = {
        "passed": all(
            result.passed
            and (
                streams[name]["fresh"] is True if freshness_threshold_seconds is not None else True
            )
            for name, result in results.items()
        ),
        "freshness_threshold_seconds": freshness_threshold_seconds,
        "snapshot_started_at_ns": snapshot_started_at_ns,
        "streams": streams,
    }
    return json.dumps(payload, indent=2)


def hourly_volume_profile(root: Path) -> pd.DataFrame:
    profiles: list[pd.DataFrame] = []
    for stream in ("book_ticker", "trade"):
        files = sorted((root / stream).glob("date=*/hour=*/*.parquet"))
        if not files:
            continue
        dataset = ds.dataset([str(path) for path in files], format="parquet")
        receive_ts_ns = (
            dataset.to_table(columns=["receive_ts_ns"])["receive_ts_ns"]
            .combine_chunks()
            .to_numpy(zero_copy_only=False)
        )
        timestamps = pd.to_datetime(receive_ts_ns, unit="ns", utc=True)
        frame = pd.DataFrame({"receive_ts_ns": receive_ts_ns, "hour_utc": timestamps.floor("h")})
        profile = frame.groupby("hour_utc", as_index=False).agg(
            rows=("receive_ts_ns", "size"),
            min_receive_ts_ns=("receive_ts_ns", "min"),
            max_receive_ts_ns=("receive_ts_ns", "max"),
        )
        hour_start_ns = profile["hour_utc"].astype("int64")
        profile["first_event_offset_s"] = (
            profile["min_receive_ts_ns"] - hour_start_ns
        ) / 1_000_000_000
        profile["last_event_offset_s"] = (
            profile["max_receive_ts_ns"] - hour_start_ns
        ) / 1_000_000_000
        profile["coverage_seconds"] = (
            profile["max_receive_ts_ns"] - profile["min_receive_ts_ns"]
        ) / 1_000_000_000
        profile["events_per_covered_second"] = profile["rows"] / profile["coverage_seconds"].clip(
            lower=1.0
        )
        profile["is_full_hour"] = (profile["first_event_offset_s"] <= 60.0) & (
            profile["last_event_offset_s"] >= 3_540.0
        )
        profile.insert(0, "stream", stream)
        profiles.append(profile)
    if not profiles:
        return pd.DataFrame()
    return pd.concat(profiles, ignore_index=True).sort_values(["hour_utc", "stream"], kind="stable")


def write_hourly_profile(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary_path, index=False)
    os.replace(temporary_path, path)
