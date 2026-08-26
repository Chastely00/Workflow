# ETF Child Prompt 09 — Volume Ratio

Goal: `GOAL-ETF-TRICKS-001`

ETF ID: `volume_ratio`

Use raw `volume` in the shared feature engine.

## Contract

Calculate the non-overlapping ratio:

```text
mean(volume[T-19:T]) / mean(volume[T-79:T-20])
```

Equivalent implementation is `volume.rolling(20).mean() / volume.rolling(60).mean().shift(20)`. Require every observation in the 20-day numerator and delayed 60-day denominator, covering 80 trading dates. Require a positive denominator. Do not zero-fill. Apply common universe, two-stage liquidity, descending ranking, tie-breaks, candidate rules, and equal weights.

## Required tests

- Hand-check numerator, denominator, and ratio.
- Prove the two windows share no dates.
- Reject one missing observation in either window.
- Reject zero denominator.
- Verify exact 80-date boundary and descending selection.

## Acceptance evidence

Return both window date ranges, observation counts, means, ratio, focused tests, candidate audit, and one allocation plan.
