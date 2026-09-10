# Quant Internship Portfolio Pack

## Project title

**Order Flow, Adverse Selection, and Inventory-Aware Market Making on BTCUSDT**

## One-line description

Built a gap-aware, reproducible microstructure research pipeline that collects public BTCUSDT
trades/top-of-book data, evaluates causal short-horizon signals with embargoed walk-forward tests,
and falsifies market-making assumptions across fees and queue-aware fill scenarios.

## Resume bullets: validated final version

- Engineered an asynchronous public-market-data pipeline with bounded queues, reconnect/backoff,
  application-level idle detection, atomic Parquet partitions, raw-data QA, and automatic refresh of
  closed-hour features after late-arriving parts.
- Built causal one-second order-flow and top-of-book features with explicit future-label isolation,
  stale-state masking, post-gap warm-up, horizon embargoes, and expanding-history hourly
  walk-forward evaluation.
- Developed an inventory-constrained market-making replay with optimistic touch and conservative
  queue-aware fills, decomposing fees, spread capture, adverse selection, markout, liquidation cost,
  drawdown, and hourly performance rather than presenting a best-case P&L.

These bullets describe implemented work and remain safe after final validation because they make no
profitability or stable-alpha claim.

## Quantified resume bullets

- Processed 13.58M best-quote updates and 4.89M trades into 24.325 valid one-second hours; retained
  system/network gaps for audit, excluded 68.97% stale or ineligible bars, and observed zero
  duplicate event IDs or second keys.
- Evaluated 75,964 one-second and 75,441 five-second out-of-sample observations across 35 embargoed
  hourly folds; achieved aggregate balanced accuracies of 0.652 and 0.587 while reporting fold
  dispersion, calibration error, and coefficient-sign stability.
- Tested 50 fee/queue/fill scenarios for symmetric and signal/inventory quoting; no scenario was
  profitable, including zero-fee optimistic fills, so the study rejected the trading hypothesis
  instead of promoting a best-case backtest.

## Two-minute interview story

1. **Motivation:** a predictive order-flow signal is not automatically an executable market-making
   edge because fills are selective, inventory accumulates, and fees can dominate.
2. **Engineering challenge:** WebSocket transport health did not guarantee application-message
   flow. The pipeline detected this, added message-idle recovery, and preserved gaps instead of
   fabricating zero-flow seconds.
3. **Research design:** features are causal, labels are isolated, boundaries use embargoes, and
   later fragmented periods are evaluated through expanding-history hourly folds.
4. **Execution realism:** touch fills are labeled optimistic; queue-aware scenarios require volume
   to clear assumed displayed depth. Results are decomposed into spread capture and adverse
   selection.
5. **What was learned:** aggregate predictive metrics can coexist with unstable recent folds and
   negative markout/economics. A strong research project can reject its initial profitability
   hypothesis while demonstrating sound methodology.
6. **Decision:** the 24-hour gate and frozen protocol are complete. Stop the current quoting
   specification; richer queue reconstruction is a separate version-2 experiment, not an attempt
   to tune away the negative result.

## Likely interviewer questions

### Why use local receive time instead of exchange event time?

Local receive time matches the sequence available to the collector and supports causal replay.
Exchange timestamps remain useful fields, but mixing them into the aggregation clock can reorder
what the strategy could have observed.

### Why Logistic regression?

It is deliberately interpretable and difficult to hide behind. The goal is to validate the data,
splits, class imbalance, calibration, and stability before introducing flexible models.

### Why not concatenate all valid rows into one ordinary train/test split?

Disconnected rows are valid individual observations but are not adjacent seconds. Hourly
walk-forward folds preserve time order, exclude gap-crossing labels, and reveal regime instability
that an aggregate random split would hide.

### Is queue-aware replay realistic?

It is more conservative than touch, but still an approximation because top-of-book data cannot
reconstruct exact price-level queue priority, cancellations, or latency. Therefore it is a
sensitivity scenario, not an execution backtest.

### What result would count as promising?

Stable out-of-sample behavior across time blocks, reasonable calibration, coefficient or mechanism
consistency, favorable post-fill markout, controlled inventory/drawdown, and positive economics
under nonzero fees and conservative fill assumptions. One best fold or zero-fee touch case is not
enough.

## Repository tour for an interviewer

- `collector.py` and `storage.py`: ingestion reliability and immutable raw storage.
- `quality.py` and `bars.py`: raw QA, causal features, eligibility, labels, and late-part refresh.
- `model.py` and `diagnostics.py`: chronological baselines, walk-forward evaluation, calibration,
  and coefficient stability.
- `simulation.py` and `sensitivity.py`: inventory-aware replay, fill assumptions, attribution, and
  robustness grid.
- `checkpoint.py`: one-command orchestration and acceptance-state manifest.
- `reports/research_log.md`: timestamped findings, incidents, fixes, and rejected interpretations.

## Language to avoid

- “profitable strategy” when only touch or zero-fee scenarios are positive;
- “backtest” without explaining fill and queue limitations;
- “24-hour sample” when only the wall-clock span, rather than valid coverage, is 24 hours;
- “causal signal” when the evidence is predictive association; and
- “production-ready” when live execution, risk controls, and operational monitoring are out of
  scope.
