from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from orderflow_mm.simulation import SimulationConfig, simulate_market_maker


def run_sensitivity_grid(
    bars: pd.DataFrame,
    fee_bps_values: Iterable[float] = (0.0, 0.5, 1.0, 2.0, 5.0),
    queue_fractions: Iterable[float] = (0.25, 0.5, 1.0, 2.0),
) -> pd.DataFrame:
    rows: list[dict[str, float | int | str]] = []
    fees = tuple(fee_bps_values)
    queues = tuple(queue_fractions)
    if not fees or not queues:
        raise ValueError("fee and queue grids cannot be empty")

    for strategy in ("symmetric", "signal_inventory"):
        for fee_bps in fees:
            touch = simulate_market_maker(
                bars,
                SimulationConfig(
                    strategy=strategy,
                    fill_model="touch",
                    maker_fee_bps=fee_bps,
                ),
            )
            rows.append(_summary_row(touch))
            for queue_fraction in queues:
                queue_aware = simulate_market_maker(
                    bars,
                    SimulationConfig(
                        strategy=strategy,
                        fill_model="queue_aware",
                        maker_fee_bps=fee_bps,
                        queue_fraction=queue_fraction,
                    ),
                )
                rows.append(_summary_row(queue_aware))
    return pd.DataFrame(rows).sort_values(
        ["strategy", "fill_model", "maker_fee_bps", "queue_fraction"],
        kind="stable",
    )


def write_sensitivity_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary_path, index=False)
    os.replace(temporary_path, path)


def _summary_row(report: dict) -> dict[str, float | int | str]:
    config = report["config"]
    transitions = max(1, report["rows"] - report["contiguous_segments"])
    return {
        "strategy": config["strategy"],
        "fill_model": config["fill_model"],
        "maker_fee_bps": config["maker_fee_bps"],
        "queue_fraction": config["queue_fraction"],
        "rows": report["rows"],
        "contiguous_segments": report["contiguous_segments"],
        "fills": report["fills"],
        "fills_per_transition": report["fills"] / transitions,
        "spread_capture": report["spread_capture"],
        "adverse_selection": report["adverse_selection"],
        "markout_1s": report["markout_1s"],
        "total_fees": report["total_fees"],
        "liquidation_adjusted_pnl": report["liquidation_adjusted_pnl"],
        "maximum_drawdown": report["maximum_drawdown"],
        "max_absolute_inventory": report["max_absolute_inventory"],
    }
