# ETF Child Prompt 02 — Monthly Sales

Goal: `GOAL-ETF-TRICKS-001`

ETF ID: `monthly_sales`

Use the authoritative design and Master Prompt. Work only inside the shared engine.

## Contract

- Signal is `monthly_sales.r18`, trailing-12-month cumulative sales growth.
- At formation date `T`, only rows with `source_available_date <= T` are visible.
- Choose latest `source_period_date`; within that period choose the latest version available by `T` with a stable source-row tie-break.
- Require period age `12*(year(T)-year(P)) + month(T)-month(P)` between 0 and 2 inclusive.
- Require finite `r18`; never fill missing values with zero.
- Apply common universe, two-stage liquidity, descending ranking, tie-breaks, candidate rules, and equal target weights.

## Required tests

- A large `r18` announced after formation must not affect the target.
- A revision available before formation must replace the earlier version; a later revision must not.
- Period ages 0 and 2 pass; age 3 fails.
- Missing `r18` is audited as excluded.
- Input row shuffling leaves the target identical.

## Acceptance evidence

Return focused tests plus a monthly candidate audit containing formation date, selected period, source availability date, age, raw `r18`, rank, and exclusion reasons. Any PIT violation is a hard failure.
