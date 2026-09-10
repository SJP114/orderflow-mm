import numpy as np
import pandas as pd
import pytest

from orderflow_mm.bars import MODEL_FEATURE_COLUMNS
from orderflow_mm.model import (
    chronological_split,
    fit_logistic_baseline,
    fit_walk_forward_logistic,
    select_longest_contiguous_segment,
    summarize_bars,
)


def _model_frame(rows: int = 1_500) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    frame = pd.DataFrame(
        {
            "second_ts_ns": np.arange(rows, dtype="int64") * 1_000_000_000,
            "timestamp": pd.date_range("2026-01-01", periods=rows, freq="s", tz="UTC"),
            "spread_bps": rng.uniform(0.5, 2.0, rows),
            "book_imbalance": rng.uniform(-1.0, 1.0, rows),
            "microprice_deviation_bps": rng.normal(0.0, 0.5, rows),
            "trade_imbalance_1s": rng.uniform(-1.0, 1.0, rows),
            "trade_imbalance_5s": rng.uniform(-1.0, 1.0, rows),
            "trade_intensity_5s": rng.poisson(10.0, rows),
            "log_mid_return_1s": rng.normal(0.0, 0.0001, rows),
            "realized_volatility_10s": rng.uniform(0.0, 0.001, rows),
        }
    )
    score = frame["book_imbalance"] + frame["trade_imbalance_1s"]
    frame["future_direction_1s"] = np.select(
        [score < -0.35, score > 0.35], [-1, 1], default=0
    ).astype("int8")
    frame["future_direction_5s"] = frame["future_direction_1s"]
    return frame


def test_chronological_split_applies_embargo() -> None:
    frame = _model_frame(1_000)
    split = chronological_split(frame, horizon=5)
    assert split.train["second_ts_ns"].max() < split.validation["second_ts_ns"].min()
    assert split.validation["second_ts_ns"].max() < split.test["second_ts_ns"].min()
    assert len(split.train) == 595
    assert len(split.validation) == 195
    assert len(split.test) == 200


def test_bar_summary_reports_training_readiness() -> None:
    summary = summarize_bars(_model_frame())
    assert summary["training_ready"]
    assert summary["duplicate_seconds"] == 0
    assert not any(summary["missing_model_features"].values())


def test_logistic_baseline_beats_majority_on_predictable_data() -> None:
    report = fit_logistic_baseline(_model_frame())
    assert report["split"]["embargo_rows"] == 1
    assert (
        report["logistic_regression"]["balanced_accuracy"]
        > report["majority_baseline"]["balanced_accuracy"]
    )
    assert set(report["features"]) == set(MODEL_FEATURE_COLUMNS)


def test_logistic_baseline_rejects_small_samples() -> None:
    with pytest.raises(ValueError, match="Continue collecting"):
        fit_logistic_baseline(_model_frame(100))


def test_longest_contiguous_segment_excludes_short_smoke_sample() -> None:
    short = _model_frame(10)
    long = _model_frame(20)
    long["second_ts_ns"] += 100_000_000_000
    long["timestamp"] += pd.Timedelta(seconds=100)
    combined = pd.concat([short, long], ignore_index=True)
    selected = select_longest_contiguous_segment(combined)
    assert len(selected) == 20
    assert selected["second_ts_ns"].min() == 100_000_000_000


def test_walk_forward_uses_only_prior_hours_with_outer_embargo() -> None:
    report = fit_walk_forward_logistic(
        _model_frame(11_000),
        horizon=5,
        minimum_history_rows=1_000,
        minimum_test_rows=300,
    )

    assert len(report["folds"]) >= 2
    assert report["out_of_sample_rows"] == sum(fold["test_rows"] for fold in report["folds"])
    assert set(report["features"]) == set(MODEL_FEATURE_COLUMNS)
    for fold in report["folds"]:
        assert fold["history_end_to_test_start_seconds"] > 5
        assert pd.Timestamp(fold["history_end"]) < pd.Timestamp(fold["test_start"])


def test_walk_forward_rejects_insufficient_closed_hour_history() -> None:
    with pytest.raises(ValueError, match="no eligible walk-forward folds"):
        fit_walk_forward_logistic(_model_frame(500), minimum_history_rows=1_000)
