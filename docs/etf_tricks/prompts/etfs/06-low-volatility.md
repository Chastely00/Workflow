# ETF Child Prompt 06 — Low Volatility

Goal: `GOAL-ETF-TRICKS-001`

ETF ID: `low_volatility`

Use the shared adjusted-return feature path and raw-close execution path.

## Contract

- Build simple `adj_close` returns over the last 60 trading-calendar dates.
- A return is valid only when both adjacent trading-day prices exist.
- Require at least 20 valid observations.
- Calculate sample standard deviation with `ddof=1` and annualize with `sqrt(252)`.
- Require a finite positive result.
- Rank ascending, then apply shared tie-breaks.
- Apply common universe, two-stage liquidity, candidate rules, and equal weights.

## Required tests

- Verify the annualized result against a hand calculation.
- Accept exactly 20 valid observations and reject 19.
- Do not create returns across missing adjacent dates.
- Reject constant-price zero volatility.
- Prove ascending selection and deterministic ties.

## Acceptance evidence

Return observation counts, raw daily-return sample, volatility, focused tests, exclusion reasons, and one target/allocation example.
