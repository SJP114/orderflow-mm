import pandas as pd
import pytest

from orderflow_mm.checkpoint import (
    CheckpointConfig,
    _complete_hour_count,
    _summarize_sensitivity,
)


def test_checkpoint_config_rejects_unsafe_identifier() -> None:
    with pytest.raises(ValueError, match="safe path component"):
        CheckpointConfig(checkpoint_id="../escape").validate()


def test_complete_hour_count_requires_both_full_streams() -> None:
    profile = pd.DataFrame(
        {
            "hour_utc": pd.to_datetime(
                ["2026-01-01T00:00Z", "2026-01-01T00:00Z", "2026-01-01T01:00Z"]
            ),
            "stream": ["book_ticker", "trade", "book_ticker"],
            "is_full_hour": [True, True, True],
        }
    )
    assert _complete_hour_count(profile) == 1


def test_sensitivity_summary_never_promotes_optimistic_case_to_robust() -> None:
    frame = pd.DataFrame(
        [
            {
                "strategy": "signal_inventory",
                "fill_model": "touch",
                "maker_fee_bps": 0.0,
                "queue_fraction": 1.0,
                "markout_1s": -1.0,
                "liquidation_adjusted_pnl": 2.0,
            },
            {
                "strategy": "signal_inventory",
                "fill_model": "queue_aware",
                "maker_fee_bps": 1.0,
                "queue_fraction": 1.0,
                "markout_1s": -2.0,
                "liquidation_adjusted_pnl": -3.0,
            },
        ]
    )
    summary = _summarize_sensitivity(frame)
    assert summary["positive_cases"] == 1
    assert summary["positive_nonzero_fee_queue_aware_cases"] == 0
