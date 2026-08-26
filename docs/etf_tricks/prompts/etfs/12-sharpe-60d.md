# ETF Child Prompt 12 — 60-Day Sharpe Ratio

Goal: `GOAL-ETF-TRICKS-001`

ETF ID: `sharpe_60d`

Use the shared adjusted-return feature path.

## Contract

For 60 complete simple daily `adj_close` returns:

```text
sharpe_60d = mean(r) / std(r, ddof=1) * sqrt(252)
```

Risk-free rate is zero. Require exactly 60 valid adjacent-date returns and a finite positive sample standard deviation. Do not accept a 20-observation minimum, interpolate gaps, or introduce another risk-free series. Apply common universe, two-stage liquidity, descending ranking, tie-breaks, candidate rules, and equal weights.

## Required tests

- Verify result against a hand calculation with `ddof=1`.
- Accept 60 observations and reject 59.
- Reject zero standard deviation.
- Prove gaps do not create cross-gap returns.
- Prove annualization and descending selection.

## Acceptance evidence

Return observation dates/count, daily mean, sample standard deviation, annualized Sharpe, focused tests, target audit, and allocation plan.
