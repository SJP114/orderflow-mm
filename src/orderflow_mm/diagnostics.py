from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np


def multiclass_brier_score(
    actual: Sequence[int] | np.ndarray,
    probabilities: np.ndarray,
    classes: Sequence[int] | np.ndarray,
) -> float:
    actual_array = np.asarray(actual)
    class_array = np.asarray(classes)
    if probabilities.ndim != 2 or probabilities.shape[0] != len(actual_array):
        raise ValueError("probabilities must have one row per actual observation")
    if probabilities.shape[1] != len(class_array):
        raise ValueError("probability columns must match classes")
    one_hot = (actual_array[:, None] == class_array[None, :]).astype("float64")
    return float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1)))


def confidence_calibration(
    actual: Sequence[int] | np.ndarray,
    prediction: Sequence[int] | np.ndarray,
    probabilities: np.ndarray,
    bin_edges: Sequence[float] = (0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
) -> dict[str, Any]:
    actual_array = np.asarray(actual)
    prediction_array = np.asarray(prediction)
    edges = np.asarray(bin_edges, dtype="float64")
    if len(actual_array) != len(prediction_array) or probabilities.shape[0] != len(actual_array):
        raise ValueError("actual, prediction, and probability rows must align")
    if len(edges) < 2 or edges[0] != 0.0 or edges[-1] != 1.0 or np.any(np.diff(edges) <= 0):
        raise ValueError("bin edges must increase from zero to one")

    confidence = probabilities.max(axis=1)
    correct = prediction_array == actual_array
    bin_ids = np.digitize(confidence, edges[1:-1], right=False)
    bins: list[dict[str, Any]] = []
    weighted_absolute_gap = 0.0
    for index in range(len(edges) - 1):
        selected = bin_ids == index
        count = int(selected.sum())
        if not count:
            continue
        mean_confidence = float(confidence[selected].mean())
        accuracy = float(correct[selected].mean())
        gap = accuracy - mean_confidence
        weighted_absolute_gap += count * abs(gap)
        bins.append(
            {
                "lower": float(edges[index]),
                "upper": float(edges[index + 1]),
                "rows": count,
                "mean_confidence": mean_confidence,
                "accuracy": accuracy,
                "calibration_gap": gap,
            }
        )
    return {
        "expected_calibration_error": (
            float(weighted_absolute_gap / len(actual_array)) if len(actual_array) else None
        ),
        "bins": bins,
    }


def coefficient_stability(
    coefficient_maps: Sequence[dict[str, dict[str, float]]],
) -> dict[str, Any]:
    if not coefficient_maps:
        raise ValueError("at least one coefficient map is required")
    classes = sorted(coefficient_maps[0])
    features = sorted(coefficient_maps[0][classes[0]])
    terms: dict[str, dict[str, dict[str, float]]] = {}
    unstable_terms: list[dict[str, Any]] = []
    for class_value in classes:
        terms[class_value] = {}
        for feature in features:
            values = np.asarray(
                [mapping[class_value][feature] for mapping in coefficient_maps],
                dtype="float64",
            )
            positive_share = float(np.mean(values > 0))
            negative_share = float(np.mean(values < 0))
            zero_share = float(np.mean(values == 0))
            sign_consistency = max(positive_share, negative_share, zero_share)
            summary = {
                "mean": float(values.mean()),
                "standard_deviation": float(values.std(ddof=0)),
                "minimum": float(values.min()),
                "maximum": float(values.max()),
                "positive_share": positive_share,
                "negative_share": negative_share,
                "sign_consistency": sign_consistency,
            }
            terms[class_value][feature] = summary
            if sign_consistency < 0.75:
                unstable_terms.append(
                    {
                        "class": class_value,
                        "feature": feature,
                        "sign_consistency": sign_consistency,
                    }
                )
    return {
        "folds": len(coefficient_maps),
        "sign_consistency_threshold": 0.75,
        "unstable_terms": unstable_terms,
        "terms": terms,
    }
