# Authoritative Prompt - Tier 1 Directional Label and Long-Opportunity Model

Status: Approved authority as of 2026-09-03. Parent: `03-tiered-ml-strategy-master-prompt.md`.

## 1. Single responsibility

Build and validate only the capital-neutral, only-long Tier 1 research layer:

```text
PIT AFML state -> cost-aware directional target {-1,+1} -> calibrated p1 -> long candidate
```

Do not build Tier 2, HRP, portfolio allocation, broker integration, or final strategy performance conclusions. Read `05` and `04` before work.

## 2. Inputs and outputs

Read only finalized AFML artifact tables, their manifests/hashes, and shared execution interfaces needed to validate semantics. Write only versioned Tier 1 target/event extensions, model/fold artifacts, out-of-fold predictions, diagnostics, and manifests. Never rewrite AFML labels or shared cost code.

Allowed inputs are PIT-safe FFD state, amount/activity, liquidity, portfolio state, IX0001/regime, and other available canonical AFML features. Unavailable VPIN, Kyle lambda, ATR, ADX, and VIX remain unavailable; do not rename a proxy as one of them.

## 3. Directional target

Use each ETF’s past-only 60-bar EWMA log-return volatility, at least 20 valid observations. Defaults are configurable: `pt_mult=2`, `sl_mult=2`, `vertical_bars=60`.

The target uses declared proportional buy/sell friction only, so it is independent of Tier 3 capital. Minimum commissions, integer shares, residual cash, and exact external basket costs belong to `08`, because they cannot be known before allocation without circularity.

```text
r_net = log(exit_value_after_rate_costs / entry_value_including_rate_costs)
upper: r_net >= +pt_mult * sigma_t0
lower: r_net <= -sl_mult * sigma_t0
```

Use daily-close path only for horizontal barrier detection. Entry is the next legal-session raw OPEN, not the signal close. A horizontal trigger is known after daily close; actual paper exit occurs at the next legal raw OPEN. Persist both the mark-to-market trigger and later execution facts; do not rewrite the label because of an adverse opening gap.

At vertical horizon, label by actual executable net return. Events without full horizon, valid volatility, legal path, or source evidence stay unresolved and are excluded.

### 3.1 Same-session double-touch policy

The daily-close baseline cannot double-touch: one close cannot be both above upper and below lower. Do not silently switch to OHLC logic.

If a future explicitly enabled PIT-safe daily OHLC path is used:

```text
open >= upper: upper first
open <= lower: lower first
open inside barriers AND high >= upper AND low <= lower:
    AMBIGUOUS_SAME_SESSION_DOUBLE_TOUCH
```

The ambiguous case receives no `+1/-1` target and is excluded from fitting. Preserve OHLC, barriers, availability, and reason. It may enter separately named optimistic/pessimistic stress bounds, never inferred label truth. Only tick/intraday sequence data can resolve its order.

## 4. Fitting and hand-off

Estimate `p1 = P(y_direction=+1 | PIT state)`. Begin with calibrated regularized logistic regression; any additional model family is a registered trial. Fit preprocessing, feature selection, weighting, and calibration only inside each training fold.

Use `t0/t1`, concurrency, and average uniqueness for sample weights, purging, and embargo. Random IID CV is prohibited. Produce fold-local and walk-forward/OOF predictions with model, feature, data, and availability lineage.

The candidate threshold is selected only on training/CPCV/validation evidence. Output `side=+1`, `p1`, diagnostics, and candidate reason—never NTD allocation or an order.

Test hand-calculated costs/barriers, raw-OPEN timing, daily-close trigger timing, unresolved tails, double-touch statuses, future append invariance, source delay, purging/embargo, and absence of future columns. The hand-off to `07` contains only PIT features, OOF/walk-forward `p1`, candidate indicator, decision time, event identifiers, and immutable lineage. In-sample Tier 1 predictions are forbidden in this hand-off.
