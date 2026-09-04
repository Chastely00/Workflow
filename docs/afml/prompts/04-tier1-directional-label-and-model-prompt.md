# Authoritative Prompt - Tier 1 Directional Label and Long-Opportunity Model

Status: Approved authority as of 2026-09-04. Parent: `03-tiered-ml-strategy-master-prompt.md`.

## 1. Single responsibility

Build and validate only the capital-neutral, only-long Tier 1 research layer:

```text
PIT AFML state -> cost-aware directional target {-1,+1} -> calibrated p1 -> long candidate
```

Do not build Tier 2, HRP, portfolio allocation, broker integration, or final strategy performance conclusions. Read `05` and `04` before work.

## 2. Inputs and outputs

Read finalized AFML artifact tables and their manifests/hashes. Because AFML v5 does not retain constituent raw OPEN, construct a read-only, versioned execution-market snapshot from manifest-declared canonical `daily_price_volume` raw OPEN, `daily_market_state`, governed calendar, and the upstream ETF holdings lineage. The snapshot may supply only actual entry/exit feasibility and raw prices; it must preserve source availability/revision/hash evidence and never become a model feature source or alter AFML labels. Write only versioned Tier 1 target/event extensions, model/fold artifacts, out-of-fold predictions, diagnostics, and manifests. Never rewrite AFML labels, canonical source artifacts, MongoDB, or shared cost code.

Allowed inputs are PIT-safe FFD state, amount/activity, liquidity, portfolio state, IX0001/regime, and other available canonical AFML features. A versioned feature extension may add `IR0001` 20/60-session annualized realised-volatility features, with each value backward as-of aligned to the decision time and marked `PIT_REVISION_UNVERIFIED` until revision/vintage evidence exists. `IR0001` realised volatility is not VIX and must never be named implied volatility. Unavailable VPIN, Kyle lambda, ATR, ADX, and VIX remain unavailable; do not rename a proxy as one of them.

Do not select `close_path_open_nav`, `close_path_high_nav`, `close_path_low_nav`, or `close_path_range` as a Tier 1 feature or execution price. They are daily-close-path bookkeeping fields, not tradable ETF Trick OHLC. The only permitted short-horizon replacement for ATR is a clearly named past-only Dollar-bar log-return standard deviation, for example `bar_log_return_std_14`; it is not ATR and is not annualized by `sqrt(252)` because Dollar-bar duration is irregular.

## 3. Directional target

Use each ETF’s past-only 60-bar EWMA log-return volatility, at least 20 valid observations. Defaults are configurable: `pt_mult=2`, `sl_mult=2`, `vertical_bars=60`.

The Tier 1 target uses exactly these declared minimum proportional frictions: `buy_cost_rate=0.001425` and `sell_cost_rate=0.003`. Treat the pair as the fixed, all-in research-label assumption: enter with `entry_price * (1 + buy_cost_rate)` and exit with `exit_price * (1 - sell_cost_rate)`. Do not reinterpret `sell_cost_rate` as a tax component and silently add a second sell-side commission rate; any such economic-policy change requires a new registered configuration and trial.

These rates are deliberately not a substitute for the constituent paper ledger. Tier 1 is capital-neutral and cannot know order fragmentation or allocated capital. Minimum one-NTD commission, integer-only shares (at least one share per submitted order), residual cash, per-ticket rounding, rolling-rebalance slices, broker-specific commission discounts, delayed/rejected orders, and exact external basket costs belong to `08`. The ledger must apply its declared policy per constituent order and persist `label_vs_execution_cost_gap`; it must not overwrite the Tier 1 target or make its cost model look identical to the research label.

```text
r_net = log(exit_value_after_rate_costs / entry_value_including_rate_costs)
upper: r_net >= +pt_mult * sigma_t0
lower: r_net <= -sl_mult * sigma_t0
```

Use daily-close path only for horizontal barrier detection. Entry is the next legal-session raw OPEN, not the signal close. A horizontal trigger is known after daily close; actual paper exit occurs at the next legal raw OPEN. Entry, exit, cost, and fill checks use original raw prices only: never adjusted prices, FFD values, or reconstructed ETF OHLC. Persist both the mark-to-market trigger and later execution facts; do not rewrite the label because of an adverse opening gap.

At vertical horizon, label by actual executable net return. Events without full horizon, valid volatility, legal path, or source evidence stay unresolved and are excluded. A missing/zero/non-positive raw price, suspension, delisting, or non-executable market state is not a synthetic zero-cost fill. Retain the reason and the pending/forced-exit lifecycle for the later ledger instead of fabricating a price.

### 3.1 Barrier observation policy

The authoritative path is daily-close-only. One daily close cannot double-touch, so the first daily close satisfying either horizontal boundary is the trigger. A bar's `close_path_high_nav`/`close_path_low_nav`, constituent high/low aggregates, and any reconstructed OHLC path are prohibited for this target. Do not silently upgrade to an OHLC/double-touch policy; only a separately evidenced PIT-safe intraday source and a newly registered trial could introduce one.

## 4. Fitting and hand-off

Estimate `p1 = P(y_direction=+1 | PIT state)`. Begin with calibrated regularized logistic regression; any additional model family is a registered trial. Fit preprocessing, feature selection, weighting, and calibration only inside each training fold.

Use `t0/t1`, concurrency, and average uniqueness for sample weights, purging, and embargo. Random IID CV is prohibited. Produce fold-local and walk-forward/OOF predictions with model, feature, data, and availability lineage.

The candidate threshold is selected only on training/CPCV/validation evidence. Output `side=+1`, `p1`, diagnostics, and candidate reason—never NTD allocation or an order.

Test hand-calculated `0.001425/0.003` costs/barriers, raw-OPEN timing, daily-close trigger timing, prohibited close-path-OHLC feature exclusion, unresolved tails, future append invariance, source delay, purging/embargo, and absence of future columns. The hand-off to `07` contains only PIT features, OOF/walk-forward `p1`, candidate indicator, decision time, event identifiers, and immutable lineage. In-sample Tier 1 predictions are forbidden in this hand-off.
