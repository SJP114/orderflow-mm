# Research log

## 2026-08-28 — First closed-hour checkpoint

### Data status

- Collector coverage used for this checkpoint: 2026-08-28 06:38:50–06:59:59 UTC.
- An earlier 15-second smoke test exists as a separate segment and is excluded from model fitting.
- Raw QA at 07:53 UTC: 499,042 top-of-book updates and 161,477 trades; zero duplicate
  identifiers, nonpositive prices/quantities, crossed quotes, or locked quotes.
- A batching defect that placed rows after an hour boundary in the preceding partition was found
  and fixed. Readers also filter by row timestamp so already-written cross-boundary batches remain
  recoverable.
- The processed table contains 1,285 rows across two segments. The longest continuous segment has
  1,270 rows; only this segment is used by the prediction baseline.

### Prediction baseline

These figures validate the pipeline; they are not a research conclusion because the continuous
sample is only about 21 minutes.

| Horizon | Model | Accuracy | Balanced accuracy | Macro F1 |
|---|---|---:|---:|---:|
| 1 second | Majority class | 0.9173 | 0.3333 | 0.3190 |
| 1 second | Balanced logistic | 0.5394 | 0.4553 | 0.3262 |
| 5 seconds | Majority class | 0.7194 | 0.3333 | 0.2789 |
| 5 seconds | Balanced logistic | 0.5336 | 0.3953 | 0.3844 |

Interpretation: class balancing improves balanced accuracy while sharply reducing ordinary
accuracy because the unchanged-price class dominates. The held-out test set contains very few
one-second upward moves, so coefficient or performance interpretation is premature.

### Market-making scenarios

All scenarios below assume a 1 bp maker fee. `touch` is optimistic; `queue_aware` requires
opposing volume to clear the assumed displayed queue. Results span two segments, and inventory is
liquidated at an estimated cost before a data gap.

| Strategy | Fill model | Fills | Fees | 1s markout | Liquidation-adjusted P&L |
|---|---|---:|---:|---:|---:|
| Symmetric | Touch | 1,753 | 14.0087 | -0.2823 | -14.9924 |
| Signal + inventory | Touch | 969 | 7.7436 | -0.2656 | -7.1551 |
| Symmetric | Queue-aware | 86 | 0.6845 | -0.2784 | -2.2362 |
| Signal + inventory | Queue-aware | 354 | 2.4362 | -0.2046 | -2.1087 |

No scenario is profitable in this short sample. Fee drag dominates the optimistic touch results,
and all aggregate one-second markouts are negative. Differences between strategies must not be
treated as evidence until the full sample, fee sensitivity, and queue assumptions are evaluated.

### Next gates

1. Continue collecting until total live coverage reaches 24 hours.
2. Build only closed UTC hours and preserve segment boundaries.
3. Repeat the prediction baseline after at least 6, 12, and 24 hours of continuous coverage.
4. Compare 0, 0.5, 1, 2, and 5 bp fee assumptions and multiple queue fractions.
5. Do not promote a result unless direction, markout, and inventory conclusions are stable across
   time blocks and conservative fill assumptions.

## 2026-08-28 — Second closed-hour checkpoint

### Data-quality finding and remediation

- Raw profile at 08:53 UTC: 840,587 top-of-book updates and 300,862 trades, occupying about
  16 MB compressed.
- Eleven receive-time gaps exceeded five seconds. The book and trade gaps had matching start/end
  times, supporting a collector/network interruption diagnosis rather than genuinely quiet trade
  flow.
- UTC 07 contained 99 seconds where the latest book observation became more than two seconds old.
- Severity: high for short-horizon labels and fill simulation, because forward-filling those
  seconds would turn missing evidence into artificial zero flow and stale prices.
- Remediation: retain the rows for audit, mark them `data_valid = false`, invalidate any label
  whose future window crosses them, and impose a ten-second feature warm-up after each interruption.
  Models and simulations filter invalid rows; the simulator liquidates inventory estimates before
  discontinuous segments instead of quoting across gaps.

After remediation, the processed table has 4,885 rows, of which 185 (3.79%) are stale or warm-up
rows. The usable data forms seven continuous segments; the longest contains 1,785 seconds and is
the only segment used for this checkpoint's prediction fit.

### Prediction baseline

| Horizon | Model | Accuracy | Balanced accuracy | Macro F1 |
|---|---|---:|---:|---:|
| 1 second | Training-majority class | 0.6246 | 0.3333 | 0.2563 |
| 1 second | Balanced logistic | 0.5182 | 0.5970 | 0.5001 |
| 5 seconds | Training-majority class | 0.1545 | 0.3333 | 0.0892 |
| 5 seconds | Balanced logistic | 0.6039 | 0.5287 | 0.5290 |

The 5-second majority baseline deteriorates sharply because the dominant class changes across the
time split. This is evidence of short-window distribution drift, not proof of a durable logistic
signal. The result remains a pipeline checkpoint until substantially longer blocks are available.

### Fee and queue sensitivity

The automated grid evaluated 50 scenarios across both strategies, touch/queue-aware fills, maker
fees of 0–5 bp, and queue-ahead assumptions of 0.25×–2× displayed size.

- Only one of 50 scenarios had positive liquidation-adjusted P&L: signal/inventory quoting under
  the optimistic touch model with exactly zero fees.
- That scenario still had negative aggregate one-second markout, so its positive P&L is not robust
  evidence of favorable post-fill selection.
- Every queue-aware scenario was negative, including all zero-fee cases.
- All scenarios with nonzero fees were negative.

Interpretation: the present evidence rejects any profitability claim. The result supports keeping
fees, queue uncertainty, stale-data masking, and markout decomposition as mandatory guardrails.

## 2026-08-28 — Third closed-hour checkpoint

- Raw profile: 1,170,994 top-of-book updates and 421,997 trades. No new gap over five seconds was
  observed after 08:04:18 UTC; uniqueness, price/quantity validity, and quote consistency still
  pass.
- Processed coverage: 8,485 seconds, including 220 invalid/warm-up rows (2.59%). Ten valid
  continuous segments are available; the longest contains 3,333 seconds.

| Horizon | Majority balanced accuracy | Logistic balanced accuracy | Logistic macro F1 |
|---|---:|---:|---:|
| 1 second | 0.3333 | 0.4790 | 0.1938 |
| 5 seconds | 0.3333 | 0.3333 | 0.1416 |

The apparent improvement from the previous checkpoint did not persist: one-second balanced
accuracy fell from 0.5970 to 0.4790, and the five-second result fell to chance. This is direct
evidence that the earlier result was unstable and should not be promoted.

At 1 bp, all four simulation scenarios remain negative. The 50-case sensitivity grid still has
only one positive case: signal/inventory quoting with optimistic touch fills and zero fees. Its
aggregate one-second markout remains negative. Every queue-aware scenario and every nonzero-fee
scenario remains negative.

## 2026-08-28 — Hourly ingestion profile

The first hourly volume profile confirms that UTC 07 and UTC 08 both have full-hour timestamp
coverage. Book-update rates moved from 116.9 to 93.3 events/second, while trade rates moved from
39.5 to 33.3 events/second. Because coverage remains complete and no additional receive gap over
five seconds appeared, this is treated as a possible activity/regime change rather than an
ingestion failure. More full hours are required before setting an automated drift threshold.

Repeated simulation reports now save summary metrics by default; fill-level records require an
explicit flag. This prevents routine 24-hour sensitivity runs from generating unnecessarily large
JSON artifacts while preserving an auditable fill-level option.

## 2026-08-28 — Fourth closed-hour checkpoint and collector recovery

### Data incident

The operating-system process and WebSocket reconnect loop remained alive, but application-level
events became intermittent after 10:55 UTC. The hourly profile records incomplete coverage for UTC
10–12 and a maximum receive-time gap of approximately 8,668 seconds. This is a high-severity
timeliness failure for one-second research; those gaps are retained as explicit discontinuities and
are never forward-filled into valid model or simulation rows.

The collector now treats ten seconds without a market-data message as a failed connection, logs
the first accepted event after every connection, and reconnects. Live QA now reports receive-time
lag and can enforce a caller-supplied freshness threshold. The affected collector was stopped
gracefully, its buffers were flushed, and a market-data-only replacement was started. The new run
confirmed active flow with trade ID 6,631,030,526; no order endpoint is used.

Raw structural QA after recovery covers 1,622,795 book updates and 577,837 trades, with zero
duplicate identifiers, invalid prices or quantities, crossed quotes, or locked quotes. The dataset
occupies about 31 MB. UTC 09 is the latest complete pre-incident hour; later partial hours remain
useful only as separately validated segments.

### Closed-hour results

The processed dataset now contains 20,741 seconds. Of these, 5,529 are invalid, stale, or warm-up
rows, reflecting the ingestion incident rather than usable market evidence. There are 18 valid
continuous segments; the longest has 3,591 rows. Chronological fitting uses only this longest
segment and preserves horizon-sized embargoes.

| Horizon | Majority balanced accuracy | Logistic balanced accuracy | Logistic macro F1 |
|---|---:|---:|---:|
| 1 second | 0.3333 | 0.5585 | 0.3842 |
| 5 seconds | 0.3333 | 0.4558 | 0.3836 |

These figures are another short-window checkpoint, not evidence of a stable predictive edge. The
selected sample is UTC 09 only, and prior checkpoints already showed substantial metric
instability.

At a 1 bp maker fee, all four strategy/fill scenarios are negative. Liquidation-adjusted P&L is
-186.02 for symmetric/touch, -25.00 for symmetric/queue-aware, -91.13 for
signal-inventory/touch, and -29.89 for signal-inventory/queue-aware. The 50-case sensitivity grid
again has exactly one positive case: signal/inventory quoting under optimistic touch fills with
zero fees. Its one-second markout is negative; every nonzero-fee case and every queue-aware case is
negative. This rejects a profitability claim under the present sample and assumptions.

## 2026-08-28 — Fifth closed-hour checkpoint

The recovered collector remained active through UTC 13 and into UTC 14. UTC 13 begins 473 seconds
after the hour because it straddles the earlier ingestion incident, so it is correctly classified
as a partial hour. The application-idle guard also detected and recovered additional short network
interruptions. They remain visible as separate segments rather than being treated as zero-flow
market states.

Raw QA at 14:54 UTC covers 3,214,854 book updates and 1,162,374 trades in approximately 61 MB.
Identifiers remain unique; prices, quantities, and quote ordering remain valid. The latest book and
trade rows lag the QA snapshot by 47 and 218 seconds respectively, within the ten-minute live
threshold. Freshness is now measured against QA snapshot start, preventing a long-running scan from
falsely declaring the captured snapshot stale when new files arrive concurrently.

Adding UTC 13 increases the processed dataset to 23,868 seconds, of which 6,158 are invalid, stale,
or post-gap warm-up rows. Fifty-four valid segments are now present, but the longest remains the
3,591-second UTC 09 segment. Consequently the one- and five-second Logistic fits use exactly the
same chronological training, validation, embargo, and test windows as the prior checkpoint; their
balanced accuracies remain 0.5585 and 0.4558. This is deliberate protection against joining
disconnected samples and is not new predictive evidence.

At 1 bp, liquidation-adjusted P&L remains negative in all four updated scenarios: -221.87
(symmetric/touch), -33.97 (symmetric/queue-aware), -111.32 (signal-inventory/touch), and -40.00
(signal-inventory/queue-aware). The sensitivity grid still has one positive case out of 50, only
under zero fees and optimistic touch fills; its aggregate one-second markout is -7.43. Every
nonzero-fee and every queue-aware case remains negative.

## 2026-08-28 — Expanding-history walk-forward evaluation

The original Logistic checkpoint intentionally fits only the longest continuous segment. That is
useful for leakage control but stops incorporating new evidence whenever a later hour is fragmented
by network interruptions. A second evaluation path now performs expanding-history tests by closed
UTC hour:

1. invalid/stale rows and labels whose future window crosses a gap are removed;
2. each fold trains only on timestamps strictly before the test hour, with an outer embargo equal
   to the prediction horizon;
3. regularization is selected on a chronological 80/20 split of prior history with a second,
   horizon-sized embargo; and
4. the selected model is refit on all embargo-safe history and evaluated once on the next hour.

Disconnected valid segments contribute independent training observations but are not made adjacent.
Five eligible test hours cover UTC 08, 09, 10, 13, and 14. UTC 11 and 12 are excluded from scoring
because each contains fewer than 300 valid labeled rows, although their earlier valid observations
may enter later expanding-history fits.

| Horizon | OOS rows | Majority balanced accuracy | Logistic balanced accuracy | Logistic macro F1 |
|---|---:|---:|---:|---:|
| 1 second | 15,126 | 0.3333 | 0.6159 | 0.4366 |
| 5 seconds | 14,911 | 0.3333 | 0.5521 | 0.5276 |

These aggregate metrics are not sufficient evidence of stable alpha. Performance declines over
time: on UTC 14, one-second balanced accuracy is 0.4893 and five-second balanced accuracy is
0.4171, both below their earlier folds. Hyperparameter choices also change across folds. The result
therefore supports continued data collection and regime-stability testing, not deployment or a
profitability claim.

Automated consistency checks confirm that aggregate confusion-matrix counts equal reported OOS
rows, fold test hours are unique and chronological, every outer history/test gap exceeds its
forecast horizon, and the feature list contains no `future_` fields. Reproducible reports are saved
as `logistic-walk-forward-1s-20260828T1528Z.json` and
`logistic-walk-forward-5s-20260828T1528Z.json`.

## 2026-08-29 — Suspend/resume recovery and late-part refresh

The host experienced extended sleep/network-unavailable periods. This is not continuous market
coverage: the raw profile now contains a maximum simultaneous book/trade receive gap of about
54,118 seconds. UTC 15 is complete, while UTC 16–22 are partial and fragmented. The collector
process remained alive, retried DNS and WebSocket connections without using any order endpoint,
and resumed active flow at 14:00 UTC on August 29. By 14:07 UTC, the latest book and trade rows were
273 seconds behind the QA snapshot and live freshness passed again.

Suspend/resume exposed a derived-data correctness issue. A collector can flush a buffered raw part
for an already processed closed hour after the first bar build. Previously, incremental
`build-bars` skipped any existing target file. It now compares the modification times of all raw
dependencies with the derived file and atomically rebuilds only stale closed-hour outputs. A unit
test recreates a late raw part and verifies that the derived hour grows from two to three rows.

After refreshing UTC 17 and processing available UTC 18–22 parts, the research table contains
50,591 seconds. Only 26,257 seconds (about 7.3 hours) are valid/non-warm-up observations; 48.10% of
rows are marked invalid or stale because the wall-clock span contains substantial suspend/network
gaps. The longest valid segment remains 3,591 seconds, so the single-segment Logistic baseline is
unchanged and the 24-hour acceptance gate is not met.

The expanding-history evaluation now has seven eligible hourly folds:

| Horizon | OOS rows | Majority balanced accuracy | Logistic balanced accuracy | Logistic macro F1 |
|---|---:|---:|---:|---:|
| 1 second | 20,744 | 0.3333 | 0.5972 | 0.4588 |
| 5 seconds | 20,513 | 0.3333 | 0.5389 | 0.5308 |

The most recent eligible fold, UTC 16, falls to balanced accuracy 0.4667 at one second and 0.3962
at five seconds. The aggregate figures therefore remain unstable across time and do not support an
alpha claim.

All four 1 bp simulation scenarios are negative: liquidation-adjusted P&L is -357.39
(symmetric/touch), -82.64 (symmetric/queue-aware), -203.22 (signal-inventory/touch), and -95.32
(signal-inventory/queue-aware). More importantly, the full 50-case grid now has zero positive
cases, including zero-fee optimistic touch fills. Aggregate one-second markout is negative in every
reported scenario. This strengthens the current rejection of profitability under the tested
design; it is not evidence about untested strategies or live execution.

## 2026-08-30 — Unified checkpoint and stability diagnostics

Another host-suspend interval delayed persistence after August 29 17:12 UTC. The collector remained
alive and resumed public market-data flow after wake. By 00:41 UTC on August 30, the latest book and
trade rows lagged the QA snapshot by about 179 seconds and freshness passed. Structural QA still
shows zero duplicate identifiers, invalid prices/quantities, crossed quotes, or locked quotes. The
long historical receive gap remains explicitly present rather than being hidden.

Late-part-aware bar building refreshed and added every available closed partition through August 29
23 UTC. The derived table now contains 63,712 seconds; 31,446 seconds (8.735 hours) are valid and
eligible after stale-state and warm-up masking. This remains below the 24-hour data gate.

The first unified research checkpoint completed without warnings at
`reports/generated/checkpoints/20260830T0024Z/checkpoint.json`. It records the `collecting` state,
links every model/simulation/sensitivity artifact, and leaves automatic profitability claims
disabled.

| Horizon | Folds | OOS rows | Balanced accuracy | Macro F1 | Log loss | Brier | Confidence ECE |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 second | 9 | 25,927 | 0.6226 | 0.4846 | 0.9587 | 0.6034 | 0.1318 |
| 5 seconds | 9 | 25,688 | 0.5719 | 0.5642 | 0.9378 | 0.5664 | 0.0677 |

Coefficient diagnostics flag six unstable class/feature terms at one second and two at five
seconds using a 75% sign-consistency threshold. Aggregate balanced accuracy therefore should not be
interpreted without the calibration and fold-stability caveats.

All four 1 bp scenario P&Ls remain negative, from -89.14 for symmetric/queue-aware to -424.32 for
symmetric/touch. Each report now contains hourly fee, spread-capture, adverse-selection, and markout
attribution across 18–21 observed hours. The 50-case sensitivity grid again has zero positive cases;
even the best zero-fee touch case has liquidation-adjusted P&L -14.23 and markout -30.98.

The new checkpoint, calibration, coefficient-stability, hourly-attribution, and late-part-refresh
paths pass formatting, lint, and all 29 tests.

## 2026-08-30 — Closed-hour refresh at 02:59 UTC

The collector remained alive and raw book/trade freshness passed with roughly 50 seconds of lag at
the pre-build snapshot. Structural raw QA continues to show zero duplicate event identifiers,
nonpositive values, crossed quotes, or locked quotes. Host sleep left UTC 01 without persisted data;
that interval is retained as an explicit coverage gap rather than imputed.

Only the newly closed UTC 00 partition was built. The derived table now contains 65,576 seconds,
of which 32,548 seconds (9.041 hours) are valid after stale-state and warm-up masking. The 24-hour
gate remains unmet. The unified checkpoint is saved at
`reports/generated/checkpoints/20260830T0259Z/checkpoint.json` with status `collecting` and
profitability claims disabled.

All four 1 bp scenario P&Ls remain negative: -434.23 (symmetric/touch), -89.10
(symmetric/queue-aware), -240.68 (signal-inventory/touch), and -104.09
(signal-inventory/queue-aware). The 50-case sensitivity grid has zero positive cases; its best
zero-fee optimistic touch case is still negative at -14.20 with negative one-second markout. These
short, discontinuous observations do not support a profitability claim. Formatting, lint, and all
29 tests pass.

## 2026-09-05 — Final 24-hour checkpoint and hypothesis decision

The frozen checkpoint `20260905T1712Z-final` contains 87,570 valid one-second rows, or 24.325
hours, across 381 eligible segments. Raw structural checks pass with zero duplicate event IDs,
invalid prices or quantities, crossed quotes, or locked quotes. The dataset spans 13,582,663
book-ticker updates and 4,891,134 trades. System suspension and network loss remain visible: only
14 UTC hours have full two-stream coverage, and 68.97% of constructed seconds are ineligible,
stale, or warm-up rows.

Expanding-history evaluation produces 35 scored folds. The one-second model covers 75,964 OOS rows
with aggregate balanced accuracy 0.6518 and macro F1 0.5201; the five-second model covers 75,441
rows with balanced accuracy 0.5870 and macro F1 0.5821. Fold ranges, latest-fold calibration, and
coefficient stability remain less favorable than the aggregates, so these results demonstrate
directional structure rather than a stable alpha claim.

All four 1 bp market-making scenarios have negative markout and negative liquidation-adjusted P&L.
The complete 50-case fee/queue/fill grid has zero positive cases, including zero-fee optimistic
touch fills. The current quoting specification is therefore rejected. Independent validation
passes 29 of 29 reproducibility and claim-safety checks; stopped-collector freshness is retained as
an explicit warning. No live orders were sent.

## 2026-09-10 — Local release candidate

The repository now bundles the 572 KB frozen checkpoint while continuing to ignore 281 MB of local
raw and processed data. A deterministic `generate-sample` command exercises the production
Parquet writer, structural QA, and causal bar builder without network access or live orders. Three
new tests cover sample validity, duplicate-fixture refusal, and the minimum warm-up window.

The final evidence figures are generated from frozen artifacts with a SHA-256 source manifest.
Missing UTC hours are rendered as zero coverage, disconnected walk-forward folds are not joined
across gaps, and all 50 sensitivity cases remain visible against a zero-P&L reference.

The release smoke command passes formatting, lint, all 36 unit tests, the isolated sample pipeline,
and 29 of 29 final-checkpoint validations both under the system runtime and a freshly synchronized
locked project environment. A GitHub Actions workflow runs the same command after publication.
Remote CI, repository visibility, licensing, and the release tag remain the only
release-engineering steps requiring a user decision or external repository.
