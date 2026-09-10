import pandas as pd

from orderflow_mm.simulation import SimulationConfig, simulate_market_maker


def _simulation_bars() -> pd.DataFrame:
    base = {
        "mid": 100.0,
        "spread": 2.0,
        "bid_price": 99.0,
        "ask_price": 101.0,
        "bid_qty": 10.0,
        "ask_qty": 10.0,
        "book_imbalance": 0.0,
        "trade_imbalance_5s": 0.0,
    }
    return pd.DataFrame(
        [
            base
            | {
                "second_ts_ns": 0,
                "timestamp": pd.Timestamp("2026-01-01T00:00:00Z"),
                "buy_qty": 0.0,
                "sell_qty": 0.0,
                "max_buy_price": float("nan"),
                "min_sell_price": float("nan"),
            },
            base
            | {
                "second_ts_ns": 1_000_000_000,
                "timestamp": pd.Timestamp("2026-01-01T00:00:01Z"),
                "buy_qty": 1.0,
                "sell_qty": 1.0,
                "max_buy_price": 101.0,
                "min_sell_price": 99.0,
            },
            base
            | {
                "second_ts_ns": 2_000_000_000,
                "timestamp": pd.Timestamp("2026-01-01T00:00:02Z"),
                "buy_qty": 0.0,
                "sell_qty": 0.0,
                "max_buy_price": float("nan"),
                "min_sell_price": float("nan"),
            },
        ]
    )


def test_touch_model_captures_the_spread_when_both_sides_fill() -> None:
    config = SimulationConfig(order_size=1.0, inventory_limit=2.0, maker_fee_bps=0.0)
    report = simulate_market_maker(_simulation_bars(), config)
    assert report["fills"] == 2
    assert report["final_inventory"] == 0.0
    assert report["spread_capture"] == 2.0
    assert report["final_marked_pnl"] == 2.0
    assert report["hourly_fill_attribution"] == [
        {
            "hour_utc": "2026-01-01T00:00:00+00:00",
            "fills": 2,
            "buy_fills": 1,
            "sell_fills": 1,
            "total_fees": 0.0,
            "spread_capture": 2.0,
            "adverse_selection": 0.0,
            "markout_1s": 2.0,
        }
    ]


def test_queue_aware_model_rejects_volume_behind_displayed_queue() -> None:
    config = SimulationConfig(
        fill_model="queue_aware",
        order_size=1.0,
        inventory_limit=2.0,
        maker_fee_bps=0.0,
        queue_fraction=1.0,
    )
    report = simulate_market_maker(_simulation_bars(), config)
    assert report["fills"] == 0
    assert report["final_marked_pnl"] == 0.0


def test_inventory_limit_is_never_exceeded() -> None:
    bars = _simulation_bars()
    bars.loc[1, "max_buy_price"] = float("nan")
    config = SimulationConfig(order_size=1.0, inventory_limit=1.0, maker_fee_bps=0.0)
    report = simulate_market_maker(bars, config)
    assert report["max_absolute_inventory"] <= 1.0


def test_gap_transition_is_not_treated_as_the_next_second() -> None:
    bars = _simulation_bars()
    bars.loc[2, "second_ts_ns"] = 10_000_000_000
    bars.loc[2, "timestamp"] = pd.Timestamp("2026-01-01T00:00:10Z")
    bars.loc[2, "min_sell_price"] = 99.0
    config = SimulationConfig(order_size=1.0, inventory_limit=2.0, maker_fee_bps=0.0)
    report = simulate_market_maker(bars, config)
    assert report["contiguous_segments"] == 2
    assert report["skipped_gap_transitions"] == 1
    assert report["fills"] == 2
