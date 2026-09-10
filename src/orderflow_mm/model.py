from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    log_loss,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from orderflow_mm.bars import MODEL_FEATURE_COLUMNS, assert_feature_columns_are_causal
from orderflow_mm.diagnostics import (
    coefficient_stability,
    confidence_calibration,
    multiclass_brier_score,
)


@dataclass(frozen=True)
class ChronologicalSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame


def load_bar_dataset(root: Path) -> pd.DataFrame:
    files = sorted(root.glob("date=*/hour=*/*.parquet"))
    if not files:
        raise ValueError(f"no research-bar files found under {root}")
    tables = [pq.ParquetFile(path).read() for path in files]
    frame = pa.concat_tables(tables, promote_options="default").to_pandas()
    frame = frame.sort_values("second_ts_ns", kind="stable").reset_index(drop=True)
    duplicate_seconds = int(frame["second_ts_ns"].duplicated().sum())
    if duplicate_seconds:
        raise ValueError(f"research bars contain {duplicate_seconds} duplicate seconds")
    return frame


def summarize_bars(frame: pd.DataFrame) -> dict[str, Any]:
    assert_feature_columns_are_causal(MODEL_FEATURE_COLUMNS)
    missing_features = {column: int(frame[column].isna().sum()) for column in MODEL_FEATURE_COLUMNS}
    time_differences = frame["second_ts_ns"].diff().dropna()
    segment_ids = _segment_ids(frame)
    segment_sizes = segment_ids.value_counts()
    invalid_rows = int((~frame["data_valid"]).sum()) if "data_valid" in frame else 0
    valid_frame = frame.loc[frame["data_valid"]].copy() if "data_valid" in frame else frame
    valid_segment_sizes = (
        _segment_ids(valid_frame).value_counts() if len(valid_frame) else pd.Series()
    )
    missing_seconds = int(
        ((time_differences / 1_000_000_000).clip(lower=1).astype("int64") - 1).sum()
    )
    labels: dict[str, dict[str, int]] = {}
    for horizon in (1, 5):
        column = f"future_direction_{horizon}s"
        counts = frame[column].value_counts(dropna=False).sort_index()
        labels[column] = {str(key): int(value) for key, value in counts.items()}
    return {
        "rows": len(frame),
        "start": _iso_timestamp(frame["timestamp"].iloc[0]) if len(frame) else None,
        "end": _iso_timestamp(frame["timestamp"].iloc[-1]) if len(frame) else None,
        "duplicate_seconds": int(frame["second_ts_ns"].duplicated().sum()),
        "contiguous_segments": int(len(segment_sizes)),
        "longest_contiguous_segment_rows": int(segment_sizes.max()) if len(frame) else 0,
        "invalid_or_stale_book_rows": invalid_rows,
        "invalid_or_stale_book_rate": float(invalid_rows / len(frame)) if len(frame) else 0.0,
        "valid_contiguous_segments": int(len(valid_segment_sizes)),
        "longest_valid_contiguous_segment_rows": (
            int(valid_segment_sizes.max()) if len(valid_segment_sizes) else 0
        ),
        "missing_seconds_between_partitions": missing_seconds,
        "missing_model_features": missing_features,
        "label_distribution": labels,
        "training_ready": len(frame) >= 1_000 and not any(missing_features.values()),
    }


def chronological_split(
    frame: pd.DataFrame,
    horizon: int,
    train_fraction: float = 0.6,
    validation_fraction: float = 0.2,
) -> ChronologicalSplit:
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("split fractions must be between zero and one")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train and validation fractions must leave a test set")

    ordered = frame.sort_values("second_ts_ns", kind="stable").reset_index(drop=True)
    train_boundary = int(len(ordered) * train_fraction)
    validation_boundary = int(len(ordered) * (train_fraction + validation_fraction))
    train = ordered.iloc[: max(0, train_boundary - horizon)].copy()
    validation = ordered.iloc[
        train_boundary : max(train_boundary, validation_boundary - horizon)
    ].copy()
    test = ordered.iloc[validation_boundary:].copy()
    if min(len(train), len(validation), len(test)) == 0:
        raise ValueError("not enough rows for chronological split and embargo")
    return ChronologicalSplit(train=train, validation=validation, test=test)


def fit_logistic_baseline(
    frame: pd.DataFrame,
    horizon: int = 1,
    minimum_rows: int = 1_000,
) -> dict[str, Any]:
    if horizon not in (1, 5):
        raise ValueError("supported horizons are 1 and 5 seconds")
    assert_feature_columns_are_causal(MODEL_FEATURE_COLUMNS)
    source_rows = len(frame)
    source_invalid_rows = int((~frame["data_valid"]).sum()) if "data_valid" in frame else 0
    if "data_valid" in frame:
        frame = frame.loc[frame["data_valid"]].copy()
    source_segments = int(_segment_ids(frame).nunique())
    frame = select_longest_contiguous_segment(frame)
    target = f"future_direction_{horizon}s"
    modeling = frame.dropna(subset=MODEL_FEATURE_COLUMNS + [target]).copy()
    if len(modeling) < minimum_rows:
        raise ValueError(
            f"need at least {minimum_rows} labeled rows; found {len(modeling)}. "
            "Continue collecting data before training."
        )

    split = chronological_split(modeling, horizon=horizon)
    x_train = split.train[MODEL_FEATURE_COLUMNS]
    y_train = split.train[target].astype("int8")
    x_validation = split.validation[MODEL_FEATURE_COLUMNS]
    y_validation = split.validation[target].astype("int8")
    x_test = split.test[MODEL_FEATURE_COLUMNS]
    y_test = split.test[target].astype("int8")
    if y_train.nunique() < 2:
        raise ValueError("training set contains fewer than two target classes")

    majority_class = int(y_train.value_counts().idxmax())
    majority_prediction = np.full(len(y_test), majority_class, dtype="int8")
    majority_metrics = _classification_metrics(y_test, majority_prediction)

    candidates: list[tuple[float, float, Pipeline]] = []
    for regularization in (0.01, 0.1, 1.0, 10.0):
        pipeline = Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        C=regularization,
                        class_weight="balanced",
                        max_iter=2_000,
                        random_state=0,
                    ),
                ),
            ]
        )
        pipeline.fit(x_train, y_train)
        validation_prediction = pipeline.predict(x_validation)
        validation_macro_f1 = f1_score(
            y_validation, validation_prediction, average="macro", zero_division=0
        )
        candidates.append((float(validation_macro_f1), regularization, pipeline))

    best_validation_f1, best_c, best_pipeline = max(candidates, key=lambda item: item[0])
    prediction = best_pipeline.predict(x_test)
    probabilities = best_pipeline.predict_proba(x_test)
    model = best_pipeline.named_steps["model"]
    test_metrics = _classification_metrics(y_test, prediction)
    test_metrics["log_loss"] = float(log_loss(y_test, probabilities, labels=model.classes_))
    labels = sorted(set(int(value) for value in np.concatenate([y_test, prediction])))
    test_metrics["confusion_matrix_labels"] = labels
    test_metrics["confusion_matrix"] = confusion_matrix(y_test, prediction, labels=labels).tolist()

    return {
        "research_question": (
            f"Do causal order-flow and top-of-book features predict the {horizon}-second "
            "future mid-price direction out of sample?"
        ),
        "horizon_seconds": horizon,
        "features": MODEL_FEATURE_COLUMNS,
        "target": target,
        "source_rows": source_rows,
        "source_invalid_or_stale_book_rows": source_invalid_rows,
        "source_contiguous_segments": source_segments,
        "segment_selection": "longest contiguous one-second segment",
        "rows": len(modeling),
        "split": {
            "embargo_rows": horizon,
            "train": _split_summary(split.train),
            "validation": _split_summary(split.validation),
            "test": _split_summary(split.test),
        },
        "selection": {
            "candidate_c": [0.01, 0.1, 1.0, 10.0],
            "selected_c": best_c,
            "validation_macro_f1": best_validation_f1,
        },
        "majority_baseline": majority_metrics | {"majority_class": majority_class},
        "logistic_regression": test_metrics,
        "standardized_coefficients": _coefficient_map(model),
    }


def fit_walk_forward_logistic(
    frame: pd.DataFrame,
    horizon: int = 1,
    minimum_history_rows: int = 3_000,
    minimum_test_rows: int = 300,
) -> dict[str, Any]:
    if horizon not in (1, 5):
        raise ValueError("supported horizons are 1 and 5 seconds")
    if minimum_history_rows <= 0 or minimum_test_rows <= 0:
        raise ValueError("minimum row thresholds must be positive")
    assert_feature_columns_are_causal(MODEL_FEATURE_COLUMNS)

    source_rows = len(frame)
    source_invalid_rows = int((~frame["data_valid"]).sum()) if "data_valid" in frame else 0
    if "data_valid" in frame:
        frame = frame.loc[frame["data_valid"]].copy()
    frame = frame.sort_values("second_ts_ns", kind="stable").reset_index(drop=True)
    source_segments = int(_segment_ids(frame).nunique()) if len(frame) else 0
    target = f"future_direction_{horizon}s"
    modeling = frame.dropna(subset=MODEL_FEATURE_COLUMNS + [target]).copy()
    modeling["test_hour_utc"] = pd.to_datetime(modeling["timestamp"], utc=True).dt.floor("h")

    folds: list[dict[str, Any]] = []
    skipped_hours: list[dict[str, Any]] = []
    actual_chunks: list[np.ndarray] = []
    prediction_chunks: list[np.ndarray] = []
    majority_chunks: list[np.ndarray] = []
    probability_chunks: list[np.ndarray] = []
    coefficient_maps: list[dict[str, dict[str, float]]] = []
    second_ns = 1_000_000_000

    for test_hour in sorted(modeling["test_hour_utc"].unique()):
        test_hour = pd.Timestamp(test_hour)
        test = modeling.loc[modeling["test_hour_utc"] == test_hour].copy()
        cutoff_ns = int(test["second_ts_ns"].min() - horizon * second_ns)
        history = modeling.loc[modeling["second_ts_ns"] < cutoff_ns].copy()
        reason = _walk_forward_skip_reason(
            history,
            test,
            target=target,
            horizon=horizon,
            minimum_history_rows=minimum_history_rows,
            minimum_test_rows=minimum_test_rows,
        )
        if reason is not None:
            skipped_hours.append(
                {"test_hour_utc": test_hour.isoformat(), "test_rows": len(test), "reason": reason}
            )
            continue

        validation_boundary = int(len(history) * 0.8)
        selection_train = history.iloc[: validation_boundary - horizon].copy()
        selection_validation = history.iloc[validation_boundary:].copy()
        x_selection_train = selection_train[MODEL_FEATURE_COLUMNS]
        y_selection_train = selection_train[target].astype("int8")
        x_selection_validation = selection_validation[MODEL_FEATURE_COLUMNS]
        y_selection_validation = selection_validation[target].astype("int8")

        candidates: list[tuple[float, float]] = []
        for regularization in (0.01, 0.1, 1.0, 10.0):
            candidate = _logistic_pipeline(regularization)
            candidate.fit(x_selection_train, y_selection_train)
            validation_prediction = candidate.predict(x_selection_validation)
            validation_macro_f1 = f1_score(
                y_selection_validation,
                validation_prediction,
                average="macro",
                zero_division=0,
            )
            candidates.append((float(validation_macro_f1), regularization))

        best_validation_f1, best_c = max(candidates, key=lambda item: item[0])
        x_history = history[MODEL_FEATURE_COLUMNS]
        y_history = history[target].astype("int8")
        x_test = test[MODEL_FEATURE_COLUMNS]
        y_test = test[target].astype("int8")
        pipeline = _logistic_pipeline(best_c)
        pipeline.fit(x_history, y_history)
        prediction = pipeline.predict(x_test)
        probabilities = pipeline.predict_proba(x_test)
        model = pipeline.named_steps["model"]

        majority_class = int(y_history.value_counts().idxmax())
        majority_prediction = np.full(len(y_test), majority_class, dtype="int8")
        model_metrics = _classification_metrics(y_test, prediction)
        model_metrics["log_loss"] = float(log_loss(y_test, probabilities, labels=model.classes_))
        model_metrics["multiclass_brier_score"] = multiclass_brier_score(
            y_test, probabilities, model.classes_
        )
        model_metrics["confidence_calibration"] = confidence_calibration(
            y_test, prediction, probabilities
        )
        labels = [-1, 0, 1]
        model_metrics["confusion_matrix_labels"] = labels
        model_metrics["confusion_matrix"] = confusion_matrix(
            y_test, prediction, labels=labels
        ).tolist()

        coefficients = _coefficient_map(model)
        history_end_ns = int(history["second_ts_ns"].iloc[-1])
        folds.append(
            {
                "test_hour_utc": test_hour.isoformat(),
                "history_rows": len(history),
                "history_start": _iso_timestamp(history["timestamp"].iloc[0]),
                "history_end": _iso_timestamp(history["timestamp"].iloc[-1]),
                "history_end_to_test_start_seconds": float(
                    (int(test["second_ts_ns"].iloc[0]) - history_end_ns) / second_ns
                ),
                "history_segments": int(_segment_ids(history).nunique()),
                "selection_train_rows": len(selection_train),
                "selection_validation_rows": len(selection_validation),
                "test_rows": len(test),
                "test_start": _iso_timestamp(test["timestamp"].iloc[0]),
                "test_end": _iso_timestamp(test["timestamp"].iloc[-1]),
                "test_segments": int(_segment_ids(test).nunique()),
                "target_distribution": _target_distribution(y_test),
                "selected_c": best_c,
                "validation_macro_f1": best_validation_f1,
                "majority_baseline": _classification_metrics(y_test, majority_prediction)
                | {"majority_class": majority_class},
                "logistic_regression": model_metrics,
                "standardized_coefficients": coefficients,
            }
        )
        actual_chunks.append(y_test.to_numpy())
        prediction_chunks.append(prediction)
        majority_chunks.append(majority_prediction)
        probability_chunks.append(probabilities)
        coefficient_maps.append(coefficients)

    if not folds:
        raise ValueError(
            "no eligible walk-forward folds; continue collecting closed-hour data or lower "
            "the explicitly chosen row thresholds"
        )

    actual = np.concatenate(actual_chunks)
    prediction = np.concatenate(prediction_chunks)
    majority_prediction = np.concatenate(majority_chunks)
    probabilities = np.concatenate(probability_chunks)
    labels = [-1, 0, 1]
    aggregate_model_metrics = _classification_metrics(actual, prediction)
    aggregate_model_metrics["log_loss"] = float(log_loss(actual, probabilities, labels=labels))
    aggregate_model_metrics["multiclass_brier_score"] = multiclass_brier_score(
        actual, probabilities, labels
    )
    aggregate_model_metrics["confidence_calibration"] = confidence_calibration(
        actual, prediction, probabilities
    )
    aggregate_model_metrics["confusion_matrix_labels"] = labels
    aggregate_model_metrics["confusion_matrix"] = confusion_matrix(
        actual, prediction, labels=labels
    ).tolist()

    return {
        "research_question": (
            f"Do causal order-flow and top-of-book features predict {horizon}-second "
            "future mid-price direction across successive closed UTC-hour blocks?"
        ),
        "horizon_seconds": horizon,
        "features": MODEL_FEATURE_COLUMNS,
        "target": target,
        "source_rows": source_rows,
        "source_invalid_or_stale_book_rows": source_invalid_rows,
        "source_contiguous_segments": source_segments,
        "modeling_rows": len(modeling),
        "method": {
            "training_window": "expanding history strictly before each test UTC hour",
            "test_window": "one closed UTC-hour partition",
            "outer_embargo_seconds": horizon,
            "regularization_selection": (
                "chronological 80/20 split of prior history with a horizon-sized embargo"
            ),
            "refit_after_selection": "selected C is refit on all embargo-safe prior history",
            "gap_handling": (
                "invalid rows and labels crossing gaps are excluded; disconnected valid segments "
                "may contribute independent rows but are never treated as adjacent"
            ),
        },
        "minimum_history_rows": minimum_history_rows,
        "minimum_test_rows": minimum_test_rows,
        "folds": folds,
        "skipped_hours": skipped_hours,
        "out_of_sample_rows": len(actual),
        "out_of_sample_start": folds[0]["test_start"],
        "out_of_sample_end": folds[-1]["test_end"],
        "aggregate_majority_baseline": _classification_metrics(actual, majority_prediction),
        "aggregate_logistic_regression": aggregate_model_metrics,
        "coefficient_stability": coefficient_stability(coefficient_maps),
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(temporary_path, path)


def select_longest_contiguous_segment(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        raise ValueError("cannot select a segment from an empty research table")
    ordered = frame.sort_values("second_ts_ns", kind="stable").reset_index(drop=True)
    segment_ids = _segment_ids(ordered)
    largest_segment = segment_ids.value_counts().idxmax()
    return ordered.loc[segment_ids == largest_segment].reset_index(drop=True)


def _classification_metrics(
    actual: pd.Series | np.ndarray, prediction: np.ndarray
) -> dict[str, float]:
    return {
        "accuracy": float(accuracy_score(actual, prediction)),
        "balanced_accuracy": float(balanced_accuracy_score(actual, prediction)),
        "macro_f1": float(f1_score(actual, prediction, average="macro", zero_division=0)),
    }


def _logistic_pipeline(regularization: float) -> Pipeline:
    return Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=regularization,
                    class_weight="balanced",
                    max_iter=2_000,
                    random_state=0,
                ),
            ),
        ]
    )


def _walk_forward_skip_reason(
    history: pd.DataFrame,
    test: pd.DataFrame,
    target: str,
    horizon: int,
    minimum_history_rows: int,
    minimum_test_rows: int,
) -> str | None:
    if len(history) < minimum_history_rows:
        return f"history rows below minimum ({len(history)} < {minimum_history_rows})"
    if len(test) < minimum_test_rows:
        return f"test rows below minimum ({len(test)} < {minimum_test_rows})"
    validation_boundary = int(len(history) * 0.8)
    if validation_boundary - horizon <= 0 or validation_boundary >= len(history):
        return "history cannot support chronological selection split and embargo"
    selection_train = history.iloc[: validation_boundary - horizon]
    selection_validation = history.iloc[validation_boundary:]
    required_classes = {-1, 0, 1}
    train_classes = set(selection_train[target].astype("int8").unique())
    history_classes = set(history[target].astype("int8").unique())
    if not required_classes.issubset(train_classes):
        return "selection training history does not contain all three target classes"
    if not required_classes.issubset(history_classes):
        return "refit history does not contain all three target classes"
    if selection_validation.empty:
        return "selection validation block is empty"
    return None


def _target_distribution(values: pd.Series) -> dict[str, int]:
    counts = values.value_counts().sort_index()
    return {str(int(key)): int(value) for key, value in counts.items()}


def _split_summary(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "rows": len(frame),
        "start": _iso_timestamp(frame["timestamp"].iloc[0]),
        "end": _iso_timestamp(frame["timestamp"].iloc[-1]),
    }


def _coefficient_map(model: LogisticRegression) -> dict[str, dict[str, float]]:
    coefficients: dict[str, dict[str, float]] = {}
    for class_value, class_coefficients in zip(model.classes_, model.coef_, strict=False):
        coefficients[str(int(class_value))] = {
            feature: float(coefficient)
            for feature, coefficient in zip(MODEL_FEATURE_COLUMNS, class_coefficients, strict=True)
        }
    return coefficients


def _iso_timestamp(value: Any) -> str:
    return pd.Timestamp(value).isoformat()


def _segment_ids(frame: pd.DataFrame) -> pd.Series:
    ordered_seconds = frame["second_ts_ns"]
    starts_new_segment = ordered_seconds.diff().ne(1_000_000_000)
    if len(starts_new_segment):
        starts_new_segment.iloc[0] = True
    return starts_new_segment.cumsum()
