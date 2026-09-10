# Final Research Report Outline

This outline is intentionally evidence-first. Replace bracketed fields only from a completed and
validated checkpoint manifest.

## 1. Answer-first summary

- Research question: do causal order-flow/top-of-book variables predict one- and five-second
  BTCUSDT mid-price direction, and can signal/inventory-aware quoting improve markout under
  defensible fill and fee assumptions?
- Data conclusion: `[coverage, complete hours, gaps, exclusions]`.
- Prediction conclusion: `[walk-forward aggregate and range across folds]`.
- Market-making conclusion: `[touch versus queue-aware, fee sensitivity, markout]`.
- Decision: `[reject / continue research / investigate a narrowly defined extension]`.

Do not lead with the best fold or best sensitivity row. Lead with the conclusion supported across
time blocks and conservative assumptions.

## 2. Data and market scope

- Venue/feed: Binance public market-data-only endpoint.
- Instrument: BTCUSDT spot.
- Raw events: individual trades and best bid/ask updates.
- Time standard: UTC local receive timestamps for partitioning and causal aggregation.
- Coverage: `[valid seconds and hours]`; wall-clock span: `[start/end]`.
- Known incidents: `[suspend/network gaps and affected partitions]`.
- Explicit non-scope: full-depth reconstruction, exact queue position, latency-sensitive execution,
  and live order submission.

## 3. Data engineering design

Describe reconnect backoff, application-message idle detection, bounded queues, immutable Parquet
parts, atomic writes, UTC partitioning, event-ID deduplication, late-part refresh, and manifest-based
checkpointing. Include a compact pipeline diagram in the final rendered version.

## 4. Data quality and eligibility

Report uniqueness, price/quantity validity, crossed/locked quotes, freshness, receive gaps, full
hours, stale-book rows, warm-up exclusions, and the exact number of valid one-second observations.
Explain why invalid seconds are retained for audit but excluded from models and simulation.

## 5. Feature and label construction

List the causal features, their decision timestamp, rolling-window behavior, and label horizons.
State that every `future_` field is excluded from the feature allowlist. Explain label invalidation
when a horizon touches an ineligible second.

## 6. Prediction evaluation

### Single-segment checkpoint

Use this only as a simple chronological sanity check. Report train/validation/test windows and
horizon embargoes.

### Expanding-history walk-forward

Report eligible/skipped hours, OOS rows, per-fold balanced accuracy and macro F1, log loss,
multiclass Brier score, calibration bins, and coefficient sign stability. Show the range and latest
fold, not just the aggregate.

## 7. Market-making replay

Document quote timing, next-second fill window, inventory limit, gap liquidation, fee assumptions,
and the difference between touch and queue-aware fills. Present spread capture, adverse selection,
markout, fees, liquidation-adjusted P&L, drawdown, and hourly attribution together.

## 8. Sensitivity and falsification

Summarize the full fee/queue grid. Count positive cases under:

- any assumption;
- nonzero fees;
- queue-aware fills; and
- both nonzero fees and queue-aware fills.

Treat zero-fee touch-only profitability as a failed robustness test, not a strategy result.

## 9. Limitations and alternative explanations

Cover top-of-book-only data, approximate queue ahead, missing latency/cancellation dynamics,
exchange-specific behavior, short/regime-concentrated samples, system-suspend gaps, and multiple
testing risk. Separate model predictiveness from executable economics.

## 10. Reproducibility

Provide the checkpoint ID, exact commands, dependency lockfile, raw/processed grains, artifact
paths, QA/test results, and a statement that no live orders were sent.

## 11. Final decision and next experiment

Choose exactly one decision based on validated evidence:

- stop: current mechanism is falsified under conservative assumptions;
- continue: collect a longer or cleaner sample without changing the hypothesis; or
- extend: test one pre-specified mechanism such as richer queue reconstruction.

Avoid proposing many unconstrained variations after a negative result.
