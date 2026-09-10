from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path

from orderflow_mm.bars import build_closed_hour_dataset
from orderflow_mm.checkpoint import CheckpointConfig, run_research_checkpoint
from orderflow_mm.collector import MarketDataCollector
from orderflow_mm.model import (
    fit_logistic_baseline,
    fit_walk_forward_logistic,
    load_bar_dataset,
    summarize_bars,
    write_report,
)
from orderflow_mm.quality import hourly_volume_profile, quality_json, write_hourly_profile
from orderflow_mm.sample import generate_sample_data
from orderflow_mm.sensitivity import run_sensitivity_grid, write_sensitivity_csv
from orderflow_mm.simulation import (
    SimulationConfig,
    simulate_market_maker,
    write_simulation_report,
)
from orderflow_mm.validation import validate_checkpoint, write_validation_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orderflow-mm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect", help="collect public trade and top-of-book data")
    collect.add_argument("--symbol", default="BTCUSDT")
    collect.add_argument("--output", type=Path, default=Path("data/raw"))
    collect.add_argument(
        "--duration", type=float, default=None, help="seconds; omit to run until stopped"
    )
    collect.add_argument("--batch-size", type=int, default=50_000)
    collect.add_argument("--flush-seconds", type=float, default=300.0)
    collect.add_argument("--idle-timeout-seconds", type=float, default=10.0)

    sample = subparsers.add_parser(
        "generate-sample", help="generate deterministic public-schema data for a smoke test"
    )
    sample.add_argument("--output", type=Path, default=Path("tmp/sample/raw"))
    sample.add_argument("--seconds", type=int, default=180)

    qa = subparsers.add_parser("qa", help="run deterministic checks on raw Parquet data")
    qa.add_argument("--input", type=Path, default=Path("data/raw"))
    qa.add_argument(
        "--freshness-threshold-seconds",
        type=float,
        default=None,
        help="also fail QA when the newest received event is older than this threshold",
    )

    qa_hourly = subparsers.add_parser("qa-hourly", help="profile event volume by UTC hour")
    qa_hourly.add_argument("--input", type=Path, default=Path("data/raw"))
    qa_hourly.add_argument(
        "--output", type=Path, default=Path("reports/generated/hourly-data-quality.csv")
    )

    bars = subparsers.add_parser("build-bars", help="build causal one-second research bars")
    bars.add_argument("--input", type=Path, default=Path("data/raw"))
    bars.add_argument("--output", type=Path, default=Path("data/processed/bars_1s"))
    bars.add_argument("--replace", action="store_true", help="rebuild derived closed-hour files")

    inspect_bars = subparsers.add_parser("inspect-bars", help="summarize research-bar quality")
    inspect_bars.add_argument("--input", type=Path, default=Path("data/processed/bars_1s"))

    baseline = subparsers.add_parser(
        "train-baseline", help="fit a chronological logistic-regression baseline"
    )
    baseline.add_argument("--input", type=Path, default=Path("data/processed/bars_1s"))
    baseline.add_argument("--horizon", type=int, choices=(1, 5), default=1)
    baseline.add_argument(
        "--report", type=Path, default=Path("reports/generated/logistic-baseline-1s.json")
    )

    walk_forward = subparsers.add_parser(
        "walk-forward", help="run expanding-history Logistic tests by closed UTC hour"
    )
    walk_forward.add_argument("--input", type=Path, default=Path("data/processed/bars_1s"))
    walk_forward.add_argument("--horizon", type=int, choices=(1, 5), default=1)
    walk_forward.add_argument("--minimum-history-rows", type=int, default=3_000)
    walk_forward.add_argument("--minimum-test-rows", type=int, default=300)
    walk_forward.add_argument(
        "--report", type=Path, default=Path("reports/generated/logistic-walk-forward-1s.json")
    )

    simulation = subparsers.add_parser("simulate", help="run the one-second market-making replay")
    simulation.add_argument("--input", type=Path, default=Path("data/processed/bars_1s"))
    simulation.add_argument(
        "--strategy", choices=("symmetric", "signal_inventory"), default="symmetric"
    )
    simulation.add_argument("--fill-model", choices=("touch", "queue_aware"), default="touch")
    simulation.add_argument("--maker-fee-bps", type=float, default=1.0)
    simulation.add_argument(
        "--include-fills", action="store_true", help="include every fill in the JSON report"
    )
    simulation.add_argument(
        "--report", type=Path, default=Path("reports/generated/simulation.json")
    )

    sensitivity = subparsers.add_parser(
        "sensitivity", help="run fee and queue-assumption simulation grids"
    )
    sensitivity.add_argument("--input", type=Path, default=Path("data/processed/bars_1s"))
    sensitivity.add_argument(
        "--output", type=Path, default=Path("reports/generated/sensitivity.csv")
    )

    checkpoint = subparsers.add_parser(
        "checkpoint", help="run and manifest a complete closed-hour research checkpoint"
    )
    checkpoint.add_argument("--raw-input", type=Path, default=Path("data/raw"))
    checkpoint.add_argument("--bars-input", type=Path, default=Path("data/processed/bars_1s"))
    checkpoint.add_argument("--output", type=Path, default=Path("reports/generated/checkpoints"))
    checkpoint.add_argument("--checkpoint-id", default=None)
    checkpoint.add_argument("--required-valid-hours", type=float, default=24.0)
    checkpoint.add_argument("--freshness-threshold-seconds", type=float, default=600.0)
    checkpoint.add_argument("--maker-fee-bps", type=float, default=1.0)
    checkpoint.add_argument("--skip-raw-qa", action="store_true")
    checkpoint.add_argument("--skip-sensitivity", action="store_true")

    validation = subparsers.add_parser(
        "validate-checkpoint", help="independently validate a completed research checkpoint"
    )
    validation.add_argument("--checkpoint", type=Path, required=True)
    validation.add_argument(
        "--report", type=Path, default=Path("reports/generated/final-validation.json")
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command == "collect":
        collector = MarketDataCollector(
            symbol=args.symbol,
            output_root=args.output,
            batch_size=args.batch_size,
            flush_seconds=args.flush_seconds,
            idle_timeout_seconds=args.idle_timeout_seconds,
        )
        stats = asyncio.run(collector.run(duration_seconds=args.duration))
        print(json.dumps(asdict(stats), indent=2))
        return

    if args.command == "generate-sample":
        print(json.dumps(generate_sample_data(args.output, seconds=args.seconds), indent=2))
        return

    if args.command == "qa":
        print(quality_json(args.input, args.freshness_threshold_seconds))
        return

    if args.command == "qa-hourly":
        profile = hourly_volume_profile(args.input)
        write_hourly_profile(profile, args.output)
        print(json.dumps({"rows": len(profile), "output": str(args.output)}, indent=2))
        return

    if args.command == "build-bars":
        written = build_closed_hour_dataset(args.input, args.output, replace=args.replace)
        print(json.dumps({"files_written": [str(path) for path in written]}, indent=2))
        return

    if args.command == "inspect-bars":
        print(json.dumps(summarize_bars(load_bar_dataset(args.input)), indent=2))
        return

    if args.command == "train-baseline":
        frame = load_bar_dataset(args.input)
        report = fit_logistic_baseline(frame, horizon=args.horizon)
        write_report(report, args.report)
        print(json.dumps({"report": str(args.report)}, indent=2))
        return

    if args.command == "walk-forward":
        frame = load_bar_dataset(args.input)
        report = fit_walk_forward_logistic(
            frame,
            horizon=args.horizon,
            minimum_history_rows=args.minimum_history_rows,
            minimum_test_rows=args.minimum_test_rows,
        )
        write_report(report, args.report)
        print(
            json.dumps(
                {
                    "report": str(args.report),
                    "folds": len(report["folds"]),
                    "out_of_sample_rows": report["out_of_sample_rows"],
                },
                indent=2,
            )
        )
        return

    if args.command == "simulate":
        frame = load_bar_dataset(args.input)
        config = SimulationConfig(
            strategy=args.strategy,
            fill_model=args.fill_model,
            maker_fee_bps=args.maker_fee_bps,
        )
        report = simulate_market_maker(frame, config)
        summary = {key: value for key, value in report.items() if key != "fill_details"}
        write_simulation_report(report if args.include_fills else summary, args.report)
        print(json.dumps(summary | {"report": str(args.report)}, indent=2))
        return

    if args.command == "sensitivity":
        frame = load_bar_dataset(args.input)
        sensitivity_frame = run_sensitivity_grid(frame)
        write_sensitivity_csv(sensitivity_frame, args.output)
        print(json.dumps({"rows": len(sensitivity_frame), "output": str(args.output)}, indent=2))
        return

    if args.command == "checkpoint":
        result = run_research_checkpoint(
            CheckpointConfig(
                raw_root=args.raw_input,
                bars_root=args.bars_input,
                report_root=args.output,
                checkpoint_id=args.checkpoint_id,
                required_valid_hours=args.required_valid_hours,
                freshness_threshold_seconds=args.freshness_threshold_seconds,
                include_raw_qa=not args.skip_raw_qa,
                include_sensitivity=not args.skip_sensitivity,
                maker_fee_bps=args.maker_fee_bps,
            )
        )
        print(
            json.dumps(
                {
                    "manifest": result["manifest_path"],
                    "status": result["acceptance"]["status"],
                    "valid_hours": result["data"]["valid_hours"],
                    "profitability_claim_status": result["acceptance"][
                        "profitability_claim_status"
                    ],
                    "warnings": result["warnings"],
                },
                indent=2,
            )
        )
        return

    if args.command == "validate-checkpoint":
        report = validate_checkpoint(args.checkpoint)
        write_validation_report(report, args.report)
        print(
            json.dumps(
                {
                    "report": str(args.report),
                    "passed": report["passed"],
                    "checks_passed": report["checks_passed"],
                    "checks_total": report["checks_total"],
                    "warnings": report["warnings"],
                },
                indent=2,
            )
        )
        if not report["passed"]:
            raise SystemExit(1)
        return

    raise RuntimeError(f"unhandled command: {args.command}")
