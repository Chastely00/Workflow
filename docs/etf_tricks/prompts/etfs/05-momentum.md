# ETF Child Prompt 05 — 12-1 Momentum

Goal: `GOAL-ETF-TRICKS-001`

ETF ID: `momentum`

Use the shared feature engine and approved price roles.

## Contract

On formation date `T`, calculate only:

```text
momentum_12_1 = adj_close[T-21] / adj_close[T-252] - 1
```

Offsets refer to exact `TRADEDAY_TWSE` positions. Both endpoint prices must exist, be finite, and be positive. Do not substitute a nearby observation and do not include the latest 21 trading days. Apply common universe, two-stage liquidity, descending ranking, tie-breaks, candidate rules, and equal weights. Raw close remains the execution price.

## Required tests

- Hand-check the two endpoints and result.
- Changing any price in `T-20:T` cannot change the signal.
- Changing `T-21` changes it; changing a future price does not.
- Missing or invalid endpoints exclude the candidate without forward fill.
- Allocation uses raw close even though selection uses adjusted close.

## Acceptance evidence

Return endpoint dates/prices, signal, focused leakage tests, one monthly target, and one raw-close allocation plan.
