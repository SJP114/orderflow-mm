# Order Flow, Adverse Selection, and Inventory-Aware Market Making

This repository studies two questions on BTC/USDT spot data:

1. Do trade flow and top-of-book imbalance predict short-horizon mid-price moves out of sample?
2. Can signal-skewed quotes improve post-fill markout and inventory risk relative to symmetric quoting?

The completed research milestone includes a reconnecting market-data collector, causal one-second
features, embargoed Logistic baselines, expanding-history walk-forward tests, an
inventory-constrained replay, fee/queue sensitivity, and an independently validated 24-hour
checkpoint. No live orders are sent by this project.

## Quick start

For a reviewer-safe run with no exchange connection and no large dataset download:

```bash
uv sync --all-extras --locked
uv run python scripts/release_smoke.py
```

That command runs formatting and lint checks, all unit tests, a deterministic 180-second sample
through the production writer/QA/bar pipeline, and the 29-check validation of the bundled frozen
result. The sample generator can also be used independently:

```bash
uv run orderflow-mm generate-sample --output tmp/sample/raw --seconds 180
uv run orderflow-mm qa --input tmp/sample/raw
uv run orderflow-mm build-bars --input tmp/sample/raw --output tmp/sample/bars
uv run orderflow-mm inspect-bars --input tmp/sample/bars
```

The live public-data workflow is separate and optional:

```bash
uv run orderflow-mm collect --duration 30
uv run orderflow-mm qa
uv run orderflow-mm qa --freshness-threshold-seconds 600
uv run orderflow-mm qa-hourly
uv run orderflow-mm build-bars
uv run orderflow-mm inspect-bars
uv run orderflow-mm train-baseline --horizon 1
uv run orderflow-mm walk-forward --horizon 1
uv run orderflow-mm simulate --strategy symmetric --fill-model touch
uv run orderflow-mm simulate --strategy signal_inventory --fill-model queue_aware
uv run orderflow-mm sensitivity
uv run orderflow-mm checkpoint --skip-raw-qa --skip-sensitivity
uv run orderflow-mm validate-checkpoint \
  --checkpoint reports/generated/checkpoints/20260905T1712Z-final/checkpoint.json
```

The default writer publishes a part after 50,000 rows or five minutes, whichever comes first.
Use a shorter `--flush-seconds` value only for a brief smoke test; frequent flushes create many
small Parquet files.

The collector treats ten seconds without any market-data message as a stale connection and
reconnects automatically. This is separate from WebSocket ping/pong health: a transport can remain
open while the application stream has stopped delivering events.

During live collection, pass a freshness threshold to `qa` so a structurally valid but stale
dataset fails the overall check. Without that option, freshness lag is still reported but does not
make historical, intentionally stopped datasets fail.

The collector subscribes to Binance's market-data-only WebSocket endpoint and writes immutable
Parquet parts beneath `data/raw/<stream>/date=YYYY-MM-DD/hour=HH/`. A JSON run manifest records
message counts, duplicates, reconnects, and queue pressure.

## MVP scope

- Instrument: `BTCUSDT`
- Streams: individual trades and best bid/ask updates
- Storage: UTC-partitioned Parquet
- Raw fields: exchange identifiers/timestamps, local receive timestamp, prices, and quantities
- Guardrails: bounded queue, reconnect backoff, in-run duplicate rejection, atomic part writes
- First labels: future 1-second and 5-second mid-price changes

Full-depth reconstruction and real-money execution are deliberately outside the first milestone.

## Research bars

`build-bars` processes only closed UTC hours. For every second, book state is selected from the
last observation available within that second, trade flow is aggregated by local receive time,
and empty seconds are filled causally from past book state. Columns beginning with `future_` are
labels and are explicitly excluded from the model-feature list.

If a collector flush publishes a late raw part for an already processed hour, the next incremental
`build-bars` run compares source and target modification times and rebuilds that derived hour
atomically. This makes suspend/resume recovery safe without rebuilding every historical hour.

## Prediction baseline

The first model is deliberately simple: a standardized, class-balanced logistic regression.
Training, validation, and test sets follow chronological order, with an embargo equal to the
forecast horizon at each boundary. Regularization is selected on validation data; the held-out
test set is compared with a majority-class baseline and is never used for model selection.

`walk-forward` complements the single longest-segment checkpoint with expanding-history tests by
closed UTC hour. Each fold selects regularization using only earlier observations, applies an
outer embargo equal to the prediction horizon, then refits on embargo-safe history and scores the
next hour. Invalid rows and labels crossing data gaps remain excluded; disconnected segments are
never treated as adjacent seconds.

Walk-forward reports also retain multiclass Brier score, confidence-calibration bins, and
standardized-coefficient sign stability across folds. These diagnostics make an apparently strong
aggregate score easier to reject when confidence is poorly calibrated or feature effects reverse
across time blocks.

## Market-making replay

The simulator quotes at second `t` and evaluates fills from aggressive trades during second
`t+1`. The `touch` model fills whenever trade prices reach a quote. The more conservative
`queue_aware` model additionally requires opposing volume to clear an assumed fraction of the
displayed top-of-book queue. Because the raw feed does not reconstruct price-level queue order,
both results are scenarios rather than claims of executable historical performance.

Simulation JSON files contain summary metrics by default. Pass `--include-fills` only when a
fill-level audit is required; this keeps repeated long-sample sensitivity runs compact.
Summary reports include hourly fill attribution for fees, spread capture, adverse selection, and
one-second markout so aggregate P&L cannot hide a small number of poor time blocks.

`sensitivity` evaluates both strategies over 0, 0.5, 1, 2, and 5 bp fees. The queue-aware
scenario additionally varies the assumed queue ahead from 0.25× to 2× displayed top-of-book size.

## Research checkpoints

`checkpoint` provides the reproducible orchestration layer for later milestones. It refreshes
closed-hour bars, records valid coverage against the 24-hour gate, runs both single-segment and
walk-forward Logistic evaluations, saves all four 1 bp simulation scenarios, optionally runs the
50-case sensitivity grid, and writes a single `checkpoint.json` manifest linking every artifact.

The manifest can move from `collecting` to `ready_for_final_validation`, but it never automatically
claims a profitable strategy. Any positive conservative scenario is labeled for manual stability
review; absent such evidence, profitability remains `not_supported`. Use `--skip-raw-qa` or
`--skip-sensitivity` for fast framework smoke runs, then run the complete command at the final data
gate.

The full evidence lifecycle and deferred acceptance gates are documented in
[`docs/research_protocol.md`](docs/research_protocol.md).

The 572KB frozen result bundle is versionable even though the 281MB local raw/processed dataset is
ignored. This lets reviewers reproduce claim validation without redistributing the raw capture.

Generate the final evidence figures directly from the frozen checkpoint:

```bash
uv run --extra report python scripts/generate_final_figures.py \
  --checkpoint-dir reports/generated/checkpoints/20260905T1712Z-final
```

Portfolio handoff materials are staged in [`docs/final_report_outline.md`](docs/final_report_outline.md)
and [`docs/portfolio_pack.md`](docs/portfolio_pack.md). Quantified claims are sourced from the
independently validated frozen checkpoint. The completed evidence-first write-up is
[`docs/final_research_report.md`](docs/final_research_report.md), and the remaining-work estimate is
tracked in [`docs/project_status.md`](docs/project_status.md).
