from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

FillModel = Literal["touch", "queue_aware"]
Strategy = Literal["symmetric", "signal_inventory"]


@dataclass(frozen=True)
class SimulationConfig:
    strategy: Strategy = "symmetric"
    fill_model: FillModel = "touch"
    order_size: float = 0.001
    inventory_limit: float = 0.01
    signal_skew_bps: float = 0.5
    inventory_penalty_bps: float = 0.5
    maker_fee_bps: float = 1.0
    queue_fraction: float = 1.0
    tick_size: float = 0.01

    def validate(self) -> None:
        if self.order_size <= 0 or self.inventory_limit <= 0:
            raise ValueError("order size and inventory limit must be positive")
        if self.order_size > self.inventory_limit:
            raise ValueError("order size cannot exceed inventory limit")
        if self.tick_size <= 0 or self.maker_fee_bps < 0 or self.queue_fraction < 0:
            raise ValueError(
                "tick size must be positive; fee and queue fraction cannot be negative"
            )


def simulate_market_maker(bars: pd.DataFrame, config: SimulationConfig) -> dict[str, Any]:
    """Replay one-second quotes under an explicit, deliberately simplified fill model."""
    config.validate()
    required = {
        "second_ts_ns",
        "timestamp",
        "mid",
        "spread",
        "bid_price",
        "ask_price",
        "bid_qty",
        "ask_qty",
        "book_imbalance",
        "trade_imbalance_5s",
        "buy_qty",
        "sell_qty",
        "max_buy_price",
        "min_sell_price",
    }
    missing = sorted(required.difference(bars.columns))
    if missing:
        raise ValueError(f"simulation input is missing columns: {missing}")
    if len(bars) < 2:
        raise ValueError("simulation requires at least two consecutive bars")

    if "data_valid" in bars:
        bars = bars.loc[bars["data_valid"]].copy()
    ordered = bars.sort_values("second_ts_ns", kind="stable").reset_index(drop=True)
    if len(ordered) < 2:
        raise ValueError("simulation requires at least two valid consecutive bars")
    inventory = 0.0
    cash = 0.0
    total_fees = 0.0
    gap_liquidation_cost = 0.0
    skipped_gap_transitions = 0
    segments = 1
    inventory_path = [inventory]
    marked_pnl_path = [cash + inventory * float(ordered.iloc[0]["mid"])]
    fills: list[dict[str, Any]] = []

    for index in range(len(ordered) - 1):
        decision = ordered.iloc[index]
        execution = ordered.iloc[index + 1]
        elapsed_ns = int(execution["second_ts_ns"]) - int(decision["second_ts_ns"])
        if elapsed_ns != 1_000_000_000:
            skipped_gap_transitions += 1
            segments += 1
            if inventory:
                decision_mid = float(decision["mid"])
                liquidation_cost = abs(inventory) * (
                    float(decision["spread"]) / 2.0 + decision_mid * config.maker_fee_bps / 10_000.0
                )
                cash += inventory * decision_mid - liquidation_cost
                gap_liquidation_cost += liquidation_cost
                inventory = 0.0
            inventory_path.append(inventory)
            marked_pnl_path.append(cash)
            continue
        quote_bid, quote_ask, signal = _quotes(decision, inventory, config)

        buy_qty = 0.0
        if inventory + config.order_size <= config.inventory_limit + 1e-12:
            buy_qty = _buy_fill_quantity(decision, execution, quote_bid, config)
        sell_qty = 0.0
        if inventory - config.order_size >= -config.inventory_limit - 1e-12:
            sell_qty = _sell_fill_quantity(decision, execution, quote_ask, config)

        for side, quantity, price in ((1, buy_qty, quote_bid), (-1, sell_qty, quote_ask)):
            if quantity <= 0:
                continue
            notional = quantity * price
            fee = notional * config.maker_fee_bps / 10_000.0
            inventory += side * quantity
            cash -= side * notional + fee
            total_fees += fee
            decision_mid = float(decision["mid"])
            execution_mid = float(execution["mid"])
            spread_capture = side * (decision_mid - price) * quantity
            adverse_selection = side * (execution_mid - decision_mid) * quantity
            fills.append(
                {
                    "decision_timestamp": pd.Timestamp(decision["timestamp"]).isoformat(),
                    "execution_timestamp": pd.Timestamp(execution["timestamp"]).isoformat(),
                    "side": "buy" if side == 1 else "sell",
                    "quantity": quantity,
                    "price": price,
                    "signal": signal,
                    "fee": fee,
                    "spread_capture": spread_capture,
                    "adverse_selection": adverse_selection,
                    "markout_1s": spread_capture + adverse_selection,
                    "inventory_after": inventory,
                }
            )

        inventory_path.append(inventory)
        marked_pnl_path.append(cash + inventory * float(execution["mid"]))

    final_mid = float(ordered.iloc[-1]["mid"])
    final_marked_pnl = cash + inventory * final_mid
    half_spread = float(ordered.iloc[-1]["spread"]) / 2.0
    liquidation_cost = abs(inventory) * (half_spread + final_mid * config.maker_fee_bps / 10_000.0)
    pnl_array = np.asarray(marked_pnl_path)
    running_max = np.maximum.accumulate(pnl_array)
    drawdown = pnl_array - running_max
    buy_fills = sum(fill_["side"] == "buy" for fill_ in fills)
    sell_fills = len(fills) - buy_fills

    return {
        "config": asdict(config),
        "assumptions": {
            "quote_lifetime_seconds": 1,
            "decision_time": "end of second t",
            "fill_window": "trade flow observed during second t+1",
            "touch": "fill when an opposing aggressive trade reaches the quoted price",
            "queue_aware": (
                "touch condition plus opposing volume must exceed queue_fraction times "
                "displayed top-of-book size; volume is not price-level reconstructed"
            ),
            "final_inventory": "marked at final mid and separately charged a liquidation estimate",
        },
        "rows": len(ordered),
        "contiguous_segments": segments,
        "skipped_gap_transitions": skipped_gap_transitions,
        "gap_liquidation_cost": gap_liquidation_cost,
        "start": pd.Timestamp(ordered.iloc[0]["timestamp"]).isoformat(),
        "end": pd.Timestamp(ordered.iloc[-1]["timestamp"]).isoformat(),
        "fills": len(fills),
        "buy_fills": buy_fills,
        "sell_fills": sell_fills,
        "total_fees": total_fees,
        "spread_capture": float(sum(fill_["spread_capture"] for fill_ in fills)),
        "adverse_selection": float(sum(fill_["adverse_selection"] for fill_ in fills)),
        "markout_1s": float(sum(fill_["markout_1s"] for fill_ in fills)),
        "final_inventory": inventory,
        "max_absolute_inventory": float(np.max(np.abs(inventory_path))),
        "final_marked_pnl": final_marked_pnl,
        "estimated_liquidation_cost": liquidation_cost,
        "liquidation_adjusted_pnl": final_marked_pnl - liquidation_cost,
        "maximum_drawdown": float(np.min(drawdown)),
        "hourly_fill_attribution": _hourly_fill_attribution(fills),
        "fill_details": fills,
    }


def _hourly_fill_attribution(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not fills:
        return []
    frame = pd.DataFrame(fills)
    frame["hour_utc"] = pd.to_datetime(frame["decision_timestamp"], utc=True).dt.floor("h")
    frame["buy_fill"] = (frame["side"] == "buy").astype("int64")
    frame["sell_fill"] = (frame["side"] == "sell").astype("int64")
    hourly = frame.groupby("hour_utc", as_index=False).agg(
        fills=("side", "size"),
        buy_fills=("buy_fill", "sum"),
        sell_fills=("sell_fill", "sum"),
        total_fees=("fee", "sum"),
        spread_capture=("spread_capture", "sum"),
        adverse_selection=("adverse_selection", "sum"),
        markout_1s=("markout_1s", "sum"),
    )
    hourly["hour_utc"] = hourly["hour_utc"].map(lambda value: pd.Timestamp(value).isoformat())
    return hourly.to_dict(orient="records")


def write_simulation_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(temporary_path, path)


def _quotes(
    row: pd.Series, inventory: float, config: SimulationConfig
) -> tuple[float, float, float]:
    mid = float(row["mid"])
    if config.strategy == "symmetric":
        return float(row["bid_price"]), float(row["ask_price"]), 0.0

    signal = float(
        np.clip(
            0.5 * float(row["book_imbalance"]) + 0.5 * float(row["trade_imbalance_5s"]),
            -1.0,
            1.0,
        )
    )
    normalized_inventory = inventory / config.inventory_limit
    shift_bps = (
        config.signal_skew_bps * signal - config.inventory_penalty_bps * normalized_inventory
    )
    reservation_mid = mid * (1.0 + shift_bps / 10_000.0)
    half_spread = max(float(row["spread"]) / 2.0, config.tick_size / 2.0)
    quote_bid = _floor_to_tick(reservation_mid - half_spread, config.tick_size)
    quote_ask = _ceil_to_tick(reservation_mid + half_spread, config.tick_size)
    quote_bid = min(quote_bid, float(row["ask_price"]) - config.tick_size)
    quote_ask = max(quote_ask, float(row["bid_price"]) + config.tick_size)
    if quote_bid >= quote_ask:
        quote_bid = quote_ask - config.tick_size
    return quote_bid, quote_ask, signal


def _buy_fill_quantity(
    decision: pd.Series,
    execution: pd.Series,
    quote_bid: float,
    config: SimulationConfig,
) -> float:
    trade_price = execution["min_sell_price"]
    if pd.isna(trade_price) or float(trade_price) > quote_bid + 1e-12:
        return 0.0
    if config.fill_model == "touch":
        return config.order_size
    queue_ahead = (
        config.queue_fraction * float(decision["bid_qty"])
        if quote_bid <= float(decision["bid_price"]) + 1e-12
        else 0.0
    )
    available = max(0.0, float(execution["sell_qty"]) - queue_ahead)
    return min(config.order_size, available)


def _sell_fill_quantity(
    decision: pd.Series,
    execution: pd.Series,
    quote_ask: float,
    config: SimulationConfig,
) -> float:
    trade_price = execution["max_buy_price"]
    if pd.isna(trade_price) or float(trade_price) < quote_ask - 1e-12:
        return 0.0
    if config.fill_model == "touch":
        return config.order_size
    queue_ahead = (
        config.queue_fraction * float(decision["ask_qty"])
        if quote_ask >= float(decision["ask_price"]) - 1e-12
        else 0.0
    )
    available = max(0.0, float(execution["buy_qty"]) - queue_ahead)
    return min(config.order_size, available)


def _floor_to_tick(price: float, tick_size: float) -> float:
    return float(np.floor(price / tick_size + 1e-9) * tick_size)


def _ceil_to_tick(price: float, tick_size: float) -> float:
    return float(np.ceil(price / tick_size - 1e-9) * tick_size)
