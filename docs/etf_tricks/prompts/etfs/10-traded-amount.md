# ETF Child Prompt 10 — Traded Amount

Goal: `GOAL-ETF-TRICKS-001`

ETF ID: `traded_amount`

Distinguish the stock-selection signal from the ETF's Daily amount output.

## Contract

- Selection signal is `ADV20 = mean(last 20 stock traded_value)` with a complete aligned 20-day window.
- Apply common universe and two-stage 0.2%/0.1% liquidity policy even though both use traded values; do not remove either gate.
- Rank ADV20 descending, then market cap descending and ticker ascending.
- Apply common candidate rules and equal weights.
- Separately compute ETF Daily amount for this and every ETF as same-day stock traded value times previous-close actual ETF weights.

## Required tests

- Hand-check ADV20 and reject 19 observations.
- Prove the liquidity ratio uses sums against IX0001 while the signal uses a stock mean.
- Prove descending rank and equal weights.
- Prove Daily ETF amount uses previous-close actual weights, not ADV20 or target weights.

## Acceptance evidence

Return ADV20, liquidity ratio components, monthly target, Daily ETF amount reconciliation, focused tests, and arbitrary-capital allocation.
