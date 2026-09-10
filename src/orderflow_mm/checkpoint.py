from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from orderflow_mm.bars import build_closed_hour_dataset
from orderflow_mm.model import (
    fit_logistic_baseline,
    fit_walk_forward_logistic,
    load_bar_dataset,
    summarize_bars,
    write_report,
)
from orderflow_mm.quality import hourly_volume_profile, quality_json, write_hourly_profile
from orderflow_mm.sensitivity import run_sensitivity_grid, write_sensitivity_csv
from orderflow_mm.simulation import (
    SimulationConfig,
    simulate_market_maker,
    write_simulation_report,
)


@dataclass(frozen=True)
class CheckpointConfig:
    raw_root: Path = Path("data/raw")
    bars_root: Path = Path("data/processed/bars_1s")
    report_root: Path = Path("reports/generated/checkpoints")
    checkpoint_id: str | None = None
    required_valid_hours: float = 24.0
    freshness_threshold_seconds: float | None = 600.0
    include_raw_qa: bool = True
    include_sensitivity: bool = True
    maker_fee_bps: float = 1.0

    def validate(self) -> None:
        if self.required_valid_hours <= 0:
            raise ValueError("required_valid_hours must be positive")
        if self.freshness_threshold_seconds is not None and self.freshness_threshold_seconds <= 0:
            raise ValueError("freshness_threshold_seconds must be positive when supplied")
        if self.maker_fee_bps < 0:
            raise ValueError("maker_fee_bps cannot be negative")
        if self.checkpoint_id is not None and (
            not self.checkpoint_id
            or Path(self.checkpoint_id).name != self.checkpoint_id
            or self.checkpoint_id in {".", ".."}
        ):
            raise ValueError("checkpoint_id must be a single safe path component")


def run_research_checkpoint(
    config: CheckpointConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    config.validate()
    as_of = (now or datetime.now(UTC)).astimezone(UTC)
    checkpoint_id = config.checkpoint_id or as_of.strftime("%Y%m%dT%H%M%SZ")
    checkpoint_root = config.report_root / checkpoint_id
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    artifacts: dict[str, Any] = {}

    raw_quality: dict[str, Any] | None = None
    full_hour_count: int | None = None
    if config.include_raw_qa:
        raw_quality = json.loads(quality_json(config.raw_root, config.freshness_threshold_seconds))
        hourly = hourly_volume_profile(config.raw_root)
        hourly_path = checkpoint_root / "hourly-data-quality.csv"
        write_hourly_profile(hourly, hourly_path)
        artifacts["hourly_data_quality"] = str(hourly_path)
        full_hour_count = _complete_hour_count(hourly)

    refreshed = build_closed_hour_dataset(config.raw_root, config.bars_root, now=as_of)
    bars = load_bar_dataset(config.bars_root)
    bar_summary = summarize_bars(bars)
    valid_seconds = int(bar_summary["rows"] - bar_summary["invalid_or_stale_book_rows"])
    valid_hours = valid_seconds / 3_600.0
    data_gate_met = valid_hours >= config.required_valid_hours

    model_artifacts: dict[str, Any] = {}
    if bar_summary["training_ready"]:
        for horizon in (1, 5):
            baseline_path = checkpoint_root / f"logistic-baseline-{horizon}s.json"
            try:
                baseline = fit_logistic_baseline(bars, horizon=horizon)
                write_report(baseline, baseline_path)
                model_artifacts[f"baseline_{horizon}s"] = str(baseline_path)
            except ValueError as exc:
                warnings.append(f"{horizon}s baseline skipped: {exc}")

            walk_forward_path = checkpoint_root / f"logistic-walk-forward-{horizon}s.json"
            try:
                walk_forward = fit_walk_forward_logistic(bars, horizon=horizon)
                write_report(walk_forward, walk_forward_path)
                model_artifacts[f"walk_forward_{horizon}s"] = str(walk_forward_path)
            except ValueError as exc:
                warnings.append(f"{horizon}s walk-forward skipped: {exc}")
    else:
        warnings.append("modeling skipped: bar summary has not reached the training gate")
    artifacts["models"] = model_artifacts

    scenario_summaries: list[dict[str, Any]] = []
    for strategy in ("symmetric", "signal_inventory"):
        for fill_model in ("touch", "queue_aware"):
            config_name = f"{strategy}-{fill_model.replace('_', '-')}"
            simulation_path = checkpoint_root / f"simulation-{config_name}.json"
            try:
                simulation = simulate_market_maker(
                    bars,
                    SimulationConfig(
                        strategy=strategy,
                        fill_model=fill_model,
                        maker_fee_bps=config.maker_fee_bps,
                    ),
                )
                summary = {key: value for key, value in simulation.items() if key != "fill_details"}
                write_simulation_report(summary, simulation_path)
                scenario_summaries.append(
                    {
                        "strategy": strategy,
                        "fill_model": fill_model,
                        "maker_fee_bps": config.maker_fee_bps,
                        "fills": summary["fills"],
                        "markout_1s": summary["markout_1s"],
                        "liquidation_adjusted_pnl": summary["liquidation_adjusted_pnl"],
                        "report": str(simulation_path),
                    }
                )
            except ValueError as exc:
                warnings.append(f"simulation {config_name} skipped: {exc}")
    artifacts["simulations"] = [row["report"] for row in scenario_summaries]

    sensitivity_summary: dict[str, Any] | None = None
    if config.include_sensitivity:
        try:
            sensitivity = run_sensitivity_grid(bars)
            sensitivity_path = checkpoint_root / "sensitivity.csv"
            write_sensitivity_csv(sensitivity, sensitivity_path)
            artifacts["sensitivity"] = str(sensitivity_path)
            sensitivity_summary = _summarize_sensitivity(sensitivity)
        except ValueError as exc:
            warnings.append(f"sensitivity skipped: {exc}")

    if sensitivity_summary is None:
        profitability_status = "not_evaluated"
    elif sensitivity_summary["positive_nonzero_fee_queue_aware_cases"] == 0:
        profitability_status = "not_supported"
    else:
        profitability_status = "requires_manual_stability_review"

    manifest = {
        "checkpoint_id": checkpoint_id,
        "as_of_utc": as_of.isoformat(),
        "scope": {
            "instrument": "BTCUSDT spot",
            "bar_grain": "one second",
            "closed_utc_hours_only": True,
            "live_orders": False,
        },
        "data": {
            "raw_quality": raw_quality,
            "full_hours_with_both_streams": full_hour_count,
            "bar_summary": bar_summary,
            "closed_hour_files_refreshed": [str(path) for path in refreshed],
            "valid_seconds": valid_seconds,
            "valid_hours": valid_hours,
        },
        "acceptance": {
            "required_valid_hours": config.required_valid_hours,
            "data_gate_met": data_gate_met,
            "status": "ready_for_final_validation" if data_gate_met else "collecting",
            "profitability_claim_status": profitability_status,
            "automatic_profitability_claims_are_disabled": True,
        },
        "scenario_summaries": scenario_summaries,
        "sensitivity_summary": sensitivity_summary,
        "artifacts": artifacts,
        "warnings": warnings,
    }
    manifest_path = checkpoint_root / "checkpoint.json"
    write_report(manifest, manifest_path)
    return manifest | {"manifest_path": str(manifest_path)}


def _complete_hour_count(profile: pd.DataFrame) -> int:
    if profile.empty:
        return 0
    grouped = profile.groupby("hour_utc", as_index=False).agg(
        streams=("stream", "nunique"),
        all_streams_full=("is_full_hour", "all"),
    )
    return int(((grouped["streams"] == 2) & grouped["all_streams_full"]).sum())


def _summarize_sensitivity(frame: pd.DataFrame) -> dict[str, Any]:
    positive = frame["liquidation_adjusted_pnl"] > 0
    nonzero_fee = frame["maker_fee_bps"] > 0
    queue_aware = frame["fill_model"] == "queue_aware"
    best = frame.loc[frame["liquidation_adjusted_pnl"].idxmax()]
    return {
        "cases": len(frame),
        "positive_cases": int(positive.sum()),
        "positive_nonzero_fee_cases": int((positive & nonzero_fee).sum()),
        "positive_queue_aware_cases": int((positive & queue_aware).sum()),
        "positive_nonzero_fee_queue_aware_cases": int((positive & nonzero_fee & queue_aware).sum()),
        "best_case": {
            "strategy": str(best["strategy"]),
            "fill_model": str(best["fill_model"]),
            "maker_fee_bps": float(best["maker_fee_bps"]),
            "queue_fraction": float(best["queue_fraction"]),
            "markout_1s": float(best["markout_1s"]),
            "liquidation_adjusted_pnl": float(best["liquidation_adjusted_pnl"]),
        },
    }
