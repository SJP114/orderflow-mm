# Research Protocol and Acceptance Framework

## Purpose

This project is a reproducible study of short-horizon order flow, adverse selection, and
inventory-aware quoting on BTCUSDT spot market data. It is not a live-trading system. Public market
data collection, derived features, models, and replay scenarios are kept separate from any order
submission capability.

## Evidence lifecycle

1. **Ingestion:** collect public trades and best bid/ask updates into immutable UTC-partitioned
   Parquet parts. Monitor uniqueness, price/quantity validity, quote consistency, freshness, and
   receive-time gaps.
2. **Closed-hour transformation:** build causal one-second bars only after a UTC hour closes. If a
   late raw part arrives, atomically refresh the affected derived hour.
3. **Data eligibility:** exclude stale book states, impose a post-gap feature warm-up, and invalidate
   every label whose future window crosses an ineligible second.
4. **Prediction evidence:** retain the longest-segment chronological Logistic baseline as a simple
   checkpoint, and use expanding-history hourly walk-forward folds for broader out-of-sample
   stability evidence.
5. **Market-making evidence:** compare symmetric and signal/inventory quoting under optimistic touch
   and conservative queue-aware fills. Decompose spread capture, adverse selection, markout, fees,
   inventory, liquidation cost, and drawdown.
6. **Sensitivity:** vary maker fees and assumed queue ahead. A result that only works with zero fees
   or optimistic touch fills is not accepted as profitability evidence.
7. **Final validation:** independently reconcile counts, time boundaries, embargoes, artifacts, and
   headline claims only after the required data gate is reached.

## Automatic checkpoint contract

Every checkpoint writes a dedicated directory containing `checkpoint.json` and its linked
artifacts. The manifest records:

- UTC creation time and closed-hour scope;
- structural/freshness QA when enabled;
- processed, invalid, and valid one-second row counts;
- valid coverage against the default 24-hour threshold;
- model and simulation artifact paths;
- scenario and sensitivity summaries;
- skipped analyses and their reasons; and
- an explicit profitability-claim status.

The only automatic data states are:

- `collecting`: fewer than the required valid seconds are available;
- `ready_for_final_validation`: the coverage threshold is met, but final validation has not yet
  passed.

There is deliberately no automatic `accepted` state. Likewise, profitability can be
`not_evaluated`, `not_supported`, or `requires_manual_stability_review`; it is never automatically
declared supported.

After the data gate is reached, `validate-checkpoint` independently reconciles the frozen manifest
and linked artifacts. It exits nonzero when a required check fails and saves an inspectable JSON
report. A stopped collector may produce a freshness warning, but raw structural checks, artifact
integrity, causal feature membership, embargoes, row counts, scenario coverage, and claim
guardrails must still pass.

## Final validation gates

The deferred final review should verify at least the following:

- 24 hours of valid one-second observations, not merely 24 wall-clock hours;
- raw event-ID uniqueness and valid prices, quantities, and quote ordering;
- documented incomplete hours and system-suspend/network gaps;
- causal feature membership and label invalidation across every horizon boundary;
- chronological inner/outer embargoes and non-overlapping walk-forward test hours;
- stability by time block rather than aggregate metrics alone;
- fee, queue, and fill-model sensitivity;
- negative markout, drawdown, and inventory risks shown alongside any favorable metric;
- reproducibility from a fresh environment; and
- a portfolio narrative that distinguishes engineering results, research findings, and rejected
  hypotheses.

## Deferred extensions

The framework now records confidence calibration, multiclass Brier score, standardized-coefficient
stability across walk-forward folds, and per-hour fill/fee/markout attribution. Once the valid-data
gate is reached, the remaining extensions are richer queue assumptions and a compact final research
report. Full-depth reconstruction and live execution remain out of scope unless separately
authorized and designed.
