from __future__ import annotations

from typing import Any

import pyarrow as pa

BOOK_TICKER_SCHEMA = pa.schema(
    [
        ("receive_ts_ns", pa.int64()),
        ("update_id", pa.int64()),
        ("symbol", pa.string()),
        ("bid_price", pa.float64()),
        ("bid_qty", pa.float64()),
        ("ask_price", pa.float64()),
        ("ask_qty", pa.float64()),
    ]
)

TRADE_SCHEMA = pa.schema(
    [
        ("receive_ts_ns", pa.int64()),
        ("event_time_ms", pa.int64()),
        ("trade_time_ms", pa.int64()),
        ("trade_id", pa.int64()),
        ("symbol", pa.string()),
        ("price", pa.float64()),
        ("qty", pa.float64()),
        ("buyer_is_maker", pa.bool_()),
    ]
)

SCHEMAS = {"book_ticker": BOOK_TICKER_SCHEMA, "trade": TRADE_SCHEMA}


def parse_message(message: dict[str, Any], receive_ts_ns: int) -> tuple[str, dict[str, Any]]:
    """Convert a Binance combined-stream message into a typed row."""
    stream = str(message.get("stream", ""))
    payload = message.get("data")
    if not isinstance(payload, dict):
        raise ValueError("message does not contain an object payload")

    if stream.endswith("@bookTicker"):
        return "book_ticker", {
            "receive_ts_ns": receive_ts_ns,
            "update_id": int(payload["u"]),
            "symbol": str(payload["s"]),
            "bid_price": float(payload["b"]),
            "bid_qty": float(payload["B"]),
            "ask_price": float(payload["a"]),
            "ask_qty": float(payload["A"]),
        }

    if stream.endswith("@trade"):
        return "trade", {
            "receive_ts_ns": receive_ts_ns,
            "event_time_ms": int(payload["E"]),
            "trade_time_ms": int(payload["T"]),
            "trade_id": int(payload["t"]),
            "symbol": str(payload["s"]),
            "price": float(payload["p"]),
            "qty": float(payload["q"]),
            "buyer_is_maker": bool(payload["m"]),
        }

    raise ValueError(f"unsupported stream: {stream}")


def event_id(stream_name: str, row: dict[str, Any]) -> int:
    if stream_name == "book_ticker":
        return int(row["update_id"])
    if stream_name == "trade":
        return int(row["trade_id"])
    raise ValueError(f"unsupported stream: {stream_name}")
