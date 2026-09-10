from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from orderflow_mm.bars import MODEL_FEATURE_COLUMNS


def validate_checkpoint(checkpoint_path: Path) -> dict[str, Any]:
    """Validate a completed research checkpoint without rerunning the research pipeline."""
    checkpoint_path = checkpoint_path.resolve()
    manifest = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    scope = manifest.get("scope", {})
    record(
        "research-only scope",
        scope.get("live_orders") is False and scope.get("closed_utc_hours_only") is True,
        "checkpoint must use closed UTC hours and must not submit live orders",
    )

    data = manifest.get("data", {})
    acceptance = manifest.get("acceptance", {})
    valid_seconds = int(data.get("valid_seconds", 0))
    valid_hours = float(data.get("valid_hours", 0.0))
    required_hours = float(acceptance.get("required_valid_hours", 0.0))
    coverage_consistent = abs(valid_hours - valid_seconds / 3_600.0) < 1e-9
    record(
        "valid coverage arithmetic",
        coverage_consistent,
        f"{valid_seconds} seconds = {valid_hours:.6f} hours",
    )
    record(
        "valid-data gate",
        acceptance.get("data_gate_met") is True
        and valid_hours >= required_hours
        and acceptance.get("status") == "ready_for_final_validation",
        f"observed {valid_hours:.3f} hours; required {required_hours:.3f} hours",
    )

    bar_summary = data.get("bar_summary", {})
    source_rows = int(bar_summary.get("rows", 0))
    invalid_rows = int(bar_summary.get("invalid_or_stale_book_rows", 0))
    record(
        "bar eligibility reconciliation",
        source_rows - invalid_rows == valid_seconds and source_rows >= valid_seconds,
        f"{source_rows} total - {invalid_rows} excluded = {valid_seconds} valid seconds",
    )
    record(
        "bar key uniqueness",
        int(bar_summary.get("duplicate_seconds", -1)) == 0,
        f"duplicate one-second keys: {bar_summary.get('duplicate_seconds')}",
    )
    missing_features = bar_summary.get("missing_model_features", {})
    record(
        "model feature completeness",
        set(missing_features) == set(MODEL_FEATURE_COLUMNS)
        and all(int(value) == 0 for value in missing_features.values()),
        "all allowlisted causal features must be present",
    )

    raw_quality = data.get("raw_quality")
    if raw_quality is None:
        record("raw structural quality", False, "raw QA was omitted from the checkpoint")
    else:
        streams = raw_quality.get("streams", {})
        expected_streams = {"book_ticker", "trade"}
        structural_pass = set(streams) == expected_streams and all(
            stream.get("passed") is True for stream in streams.values()
        )
        record(
            "raw structural quality",
            structural_pass,
            "book/trade uniqueness, domain, and quote-order checks must pass",
        )
        if raw_quality.get("passed") is not True:
            warnings.append(
                "Checkpoint-level raw QA is not fresh. This is acceptable only because collection "
                "was intentionally stopped; structural stream checks still pass."
            )

    artifacts = manifest.get("artifacts", {})
    models = artifacts.get("models", {})
    expected_models = {
        "baseline_1s",
        "baseline_5s",
        "walk_forward_1s",
        "walk_forward_5s",
    }
    record(
        "model artifact set",
        set(models) == expected_models,
        f"found model artifacts: {sorted(models)}",
    )

    for name in sorted(expected_models & set(models)):
        path = _resolve_artifact(checkpoint_path, models[name])
        exists = path.is_file()
        record(f"artifact exists: {name}", exists, str(path))
        if not exists:
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        features = report.get("features", [])
        record(
            f"causal features: {name}",
            features == MODEL_FEATURE_COLUMNS
            and not any(str(feature).startswith("future_") for feature in features),
            f"features: {features}",
        )
        if name.startswith("baseline_"):
            _validate_baseline(name, report, record)
        else:
            _validate_walk_forward(name, report, record)

    scenario_rows = manifest.get("scenario_summaries", [])
    expected_scenarios = {
        ("symmetric", "touch"),
        ("symmetric", "queue_aware"),
        ("signal_inventory", "touch"),
        ("signal_inventory", "queue_aware"),
    }
    observed_scenarios = {(row.get("strategy"), row.get("fill_model")) for row in scenario_rows}
    record(
        "simulation scenario matrix",
        observed_scenarios == expected_scenarios and len(scenario_rows) == 4,
        f"found scenarios: {sorted(observed_scenarios)}",
    )
    for row in scenario_rows:
        path = _resolve_artifact(checkpoint_path, row.get("report", ""))
        record(
            f"simulation artifact: {row.get('strategy')}/{row.get('fill_model')}",
            path.is_file(),
            str(path),
        )

    sensitivity_summary = manifest.get("sensitivity_summary") or {}
    sensitivity_path = _resolve_artifact(checkpoint_path, artifacts.get("sensitivity", ""))
    sensitivity_rows = _csv_row_count(sensitivity_path) if sensitivity_path.is_file() else -1
    expected_cases = int(sensitivity_summary.get("cases", 0))
    record(
        "sensitivity grid",
        expected_cases == 50 and sensitivity_rows == expected_cases,
        f"manifest cases: {expected_cases}; CSV rows: {sensitivity_rows}",
    )

    robust_positive = int(sensitivity_summary.get("positive_nonzero_fee_queue_aware_cases", -1))
    profitability_status = acceptance.get("profitability_claim_status")
    claim_consistent = (robust_positive == 0 and profitability_status == "not_supported") or (
        robust_positive > 0 and profitability_status == "requires_manual_stability_review"
    )
    record(
        "profitability claim guardrail",
        claim_consistent and acceptance.get("automatic_profitability_claims_are_disabled") is True,
        f"robust-positive cases: {robust_positive}; status: {profitability_status}",
    )

    for warning in manifest.get("warnings", []):
        warnings.append(f"Checkpoint warning: {warning}")

    passed = all(check["passed"] for check in checks)
    return {
        "checkpoint": str(checkpoint_path),
        "validated_at": datetime.now().astimezone().isoformat(),
        "passed": passed,
        "decision": "research_artifact_validated" if passed else "validation_failed",
        "strategy_conclusion": profitability_status,
        "checks_passed": sum(check["passed"] for check in checks),
        "checks_total": len(checks),
        "checks": checks,
        "warnings": warnings,
    }


def write_validation_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary.replace(path)


def _resolve_artifact(checkpoint_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.is_file():
        return path.resolve()
    local = checkpoint_path.parent / path.name
    return local.resolve()


def _validate_baseline(name: str, report: dict[str, Any], record: Any) -> None:
    horizon = int(report.get("horizon_seconds", 0))
    split = report.get("split", {})
    try:
        train_end = datetime.fromisoformat(split["train"]["end"])
        validation_start = datetime.fromisoformat(split["validation"]["start"])
        validation_end = datetime.fromisoformat(split["validation"]["end"])
        test_start = datetime.fromisoformat(split["test"]["start"])
        chronological = train_end < validation_start <= validation_end < test_start
        embargo_ok = int(split.get("embargo_rows", -1)) == horizon
    except (KeyError, TypeError, ValueError):
        chronological = False
        embargo_ok = False
    record(
        f"chronological split: {name}",
        chronological and embargo_ok,
        f"horizon and embargo: {horizon}s",
    )


def _validate_walk_forward(name: str, report: dict[str, Any], record: Any) -> None:
    horizon = int(report.get("horizon_seconds", 0))
    folds = report.get("folds", [])
    test_hours = [str(fold.get("test_hour_utc")) for fold in folds]
    ordered_unique = bool(folds) and test_hours == sorted(set(test_hours))
    embargo_ok = all(
        float(fold.get("history_end_to_test_start_seconds", 0.0)) > horizon for fold in folds
    )
    fold_rows = sum(int(fold.get("test_rows", 0)) for fold in folds)
    oos_rows = int(report.get("out_of_sample_rows", -1))
    matrix = report.get("aggregate_logistic_regression", {}).get("confusion_matrix", [])
    confusion_rows = sum(sum(int(value) for value in row) for row in matrix)
    record(
        f"walk-forward chronology: {name}",
        ordered_unique and embargo_ok,
        f"folds: {len(folds)}; horizon: {horizon}s",
    )
    record(
        f"walk-forward row reconciliation: {name}",
        fold_rows == oos_rows == confusion_rows,
        f"fold rows: {fold_rows}; OOS rows: {oos_rows}; confusion rows: {confusion_rows}",
    )


def _csv_row_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))
