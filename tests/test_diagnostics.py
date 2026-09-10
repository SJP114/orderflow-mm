import numpy as np

from orderflow_mm.diagnostics import (
    coefficient_stability,
    confidence_calibration,
    multiclass_brier_score,
)


def test_perfect_probabilities_have_zero_brier_and_calibration_error() -> None:
    actual = np.array([-1, 0, 1])
    probabilities = np.eye(3)
    prediction = np.array([-1, 0, 1])
    assert multiclass_brier_score(actual, probabilities, [-1, 0, 1]) == 0.0
    calibration = confidence_calibration(actual, prediction, probabilities)
    assert calibration["expected_calibration_error"] == 0.0


def test_coefficient_stability_flags_sign_changes() -> None:
    mappings = [
        {"1": {"feature": 1.0}},
        {"1": {"feature": -1.0}},
        {"1": {"feature": 0.5}},
        {"1": {"feature": -0.5}},
    ]
    stability = coefficient_stability(mappings)
    assert stability["terms"]["1"]["feature"]["sign_consistency"] == 0.5
    assert stability["unstable_terms"] == [
        {"class": "1", "feature": "feature", "sign_consistency": 0.5}
    ]
