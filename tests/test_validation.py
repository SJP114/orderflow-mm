import csv
import json
from pathlib import Path

from orderflow_mm.bars import MODEL_FEATURE_COLUMNS
from orderflow_mm.validation import validate_checkpoint, write_validation_report


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _model_report(horizon: int, walk_forward: bool) -> dict:
    base = {
        "horizon_seconds": horizon,
        "features": MODEL_FEATURE_COLUMNS,
    }
    if not walk_forward:
        return base | {
            "split": {
                "embargo_rows": horizon,
                "train": {"end": "2026-01-01T00:00:00+00:00"},
                "validation": {
                    "start": "2026-01-01T00:00:10+00:00",
                    "end": "2026-01-01T01:00:00+00:00",
                },
                "test": {"start": "2026-01-01T01:00:10+00:00"},
            }
        }
    return base | {
        "out_of_sample_rows": 3,
        "aggregate_logistic_regression": {"confusion_matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]},
        "folds": [
            {
                "test_hour_utc": "2026-01-01T02:00:00+00:00",
                "test_rows": 3,
                "history_end_to_test_start_seconds": horizon + 1,
            }
        ],
    }


def _checkpoint_fixture(tmp_path: Path) -> Path:
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()
    models = {}
    for horizon in (1, 5):
        for kind in ("baseline", "walk_forward"):
            name = f"{kind}_{horizon}s"
            path = checkpoint_dir / f"{name}.json"
            _write_json(path, _model_report(horizon, walk_forward=kind == "walk_forward"))
            models[name] = str(path)

    simulations = []
    scenario_rows = []
    for strategy in ("symmetric", "signal_inventory"):
        for fill_model in ("touch", "queue_aware"):
            path = checkpoint_dir / f"{strategy}-{fill_model}.json"
            _write_json(path, {"strategy": strategy, "fill_model": fill_model})
            simulations.append(str(path))
            scenario_rows.append(
                {"strategy": strategy, "fill_model": fill_model, "report": str(path)}
            )

    sensitivity_path = checkpoint_dir / "sensitivity.csv"
    with sensitivity_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case"])
        writer.writeheader()
        writer.writerows({"case": index} for index in range(50))

    valid_seconds = 86_400
    manifest = {
        "scope": {"live_orders": False, "closed_utc_hours_only": True},
        "data": {
            "valid_seconds": valid_seconds,
            "valid_hours": 24.0,
            "bar_summary": {
                "rows": valid_seconds + 10,
                "invalid_or_stale_book_rows": 10,
                "duplicate_seconds": 0,
                "missing_model_features": {feature: 0 for feature in MODEL_FEATURE_COLUMNS},
            },
            "raw_quality": {
                "passed": True,
                "streams": {
                    "book_ticker": {"passed": True},
                    "trade": {"passed": True},
                },
            },
        },
        "acceptance": {
            "required_valid_hours": 24.0,
            "data_gate_met": True,
            "status": "ready_for_final_validation",
            "profitability_claim_status": "not_supported",
            "automatic_profitability_claims_are_disabled": True,
        },
        "scenario_summaries": scenario_rows,
        "sensitivity_summary": {
            "cases": 50,
            "positive_nonzero_fee_queue_aware_cases": 0,
        },
        "artifacts": {
            "models": models,
            "simulations": simulations,
            "sensitivity": str(sensitivity_path),
        },
        "warnings": [],
    }
    checkpoint_path = checkpoint_dir / "checkpoint.json"
    _write_json(checkpoint_path, manifest)
    return checkpoint_path


def test_validate_checkpoint_accepts_complete_research_artifact(tmp_path: Path) -> None:
    report = validate_checkpoint(_checkpoint_fixture(tmp_path))

    assert report["passed"] is True
    assert report["checks_passed"] == report["checks_total"]
    assert report["strategy_conclusion"] == "not_supported"


def test_validate_checkpoint_rejects_future_feature(tmp_path: Path) -> None:
    checkpoint_path = _checkpoint_fixture(tmp_path)
    model_path = checkpoint_path.parent / "baseline_1s.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    model["features"] = [*MODEL_FEATURE_COLUMNS, "future_direction_1s"]
    _write_json(model_path, model)

    report = validate_checkpoint(checkpoint_path)

    assert report["passed"] is False
    failed = {check["name"] for check in report["checks"] if not check["passed"]}
    assert "causal features: baseline_1s" in failed


def test_stopped_collection_freshness_is_a_warning(tmp_path: Path) -> None:
    checkpoint_path = _checkpoint_fixture(tmp_path)
    manifest = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    manifest["data"]["raw_quality"]["passed"] = False
    _write_json(checkpoint_path, manifest)

    report = validate_checkpoint(checkpoint_path)

    assert report["passed"] is True
    assert len(report["warnings"]) == 1


def test_write_validation_report_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "reports" / "validation.json"
    write_validation_report({"passed": True}, path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"passed": True}
    assert not path.with_suffix(".json.tmp").exists()
