# ETF Child Prompt 11 — Turnover

Goal: `GOAL-ETF-TRICKS-001`

ETF ID: `turnover`

Use the shared daily feature and accounting paths.

## Contract

```text
turnover_20d = mean(last 20 daily_price_volume.turnover observations)
```

Require 20 finite observations and do not fill missing values with zero. Preserve the DataAnalysts turnover unit; do not rescale unless the manifest contract explicitly requires normalization for all rows. Apply common universe, two-stage liquidity, descending ranking, tie-breaks, candidate rules, and equal weights.

## Required tests

- Hand-check the 20-day mean.
- Reject 19 valid observations and any non-finite value.
- Prove no implicit percentage scaling changes rank or audit values.
- Prove descending rank, deterministic ties, and equal weights.

## Acceptance evidence

Return source unit/schema evidence, observation count, raw mean, selected target, focused tests, and an allocation plan using a non-default capital amount.
