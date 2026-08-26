# ETF Child Prompt 13 — 60-Day Sortino Ratio

Goal: `GOAL-ETF-TRICKS-001`

ETF ID: `sortino_60d`

Use the shared adjusted-return feature path and the exact LPM2 denominator.

## Contract

With 60 complete simple daily `adj_close` returns and `MAR_d = 0`:

```text
downside_deviation = sqrt(mean(min(r - MAR_d, 0)^2 over all 60 days))
sortino_60d = (mean(r) - MAR_d) / downside_deviation * sqrt(252)
```

Positive days contribute zero inside the downside mean. Do not calculate a standard deviation over negative-only rows. Require a finite positive downside deviation. Apply common universe, two-stage liquidity, descending ranking, tie-breaks, candidate rules, and equal weights.

## Required tests

- Hand-check the LPM2 value using all 60 denominator slots.
- Accept 60 observations and reject 59.
- Reject a zero downside deviation.
- Prove positive days contribute zero rather than being removed.
- Prove annualization, descending rank, and deterministic ties.

## Acceptance evidence

Return observation count, mean return, squared downside contributions, downside deviation, annualized Sortino, focused tests, target audit, and arbitrary-capital allocation.
