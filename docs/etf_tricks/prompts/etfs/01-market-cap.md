# ETF Child Prompt 01 — Market Cap

Goal: `GOAL-ETF-TRICKS-001`

ETF ID: `market_cap`

Read `AGENTS.md`, the approved design spec, `01-master-prompt.md`, and the implementation plan before acting. Implement or review this ETF only through the shared registry, PIT feature, universe, execution, result, allocation, and validation paths. Do not create a separate backtester.

## Contract

- At month-end formation date, use finite positive `daily_price_volume.market_cap` from that same date.
- Apply the common TWSE+TPEx universe and two-stage 0.2% then 0.1% liquidity policy.
- Rank market cap descending, then ADV20 descending, market cap descending, and ticker ascending using stable sorting.
- Select at most ten; apply the common one-to-four and zero-candidate rules.
- This is the only ETF using market-cap target weights:
  `weight_i = market_cap_i / sum(selected market_cap)`.
- Do not cap a constituent and do not substitute free-float market cap.
- Form at month `M` close and execute gradually across actual `TRADEDAY_TWSE` dates in `M+1`.

## Required tests

- Reject zero, negative, infinite, and missing market cap.
- Prove future dates cannot affect the month-end value or selection.
- Prove weights are positive and sum to one within Decimal/numeric tolerance.
- Prove a tied value uses ADV20 then ticker deterministically.
- Prove arbitrary-capital allocation changes integer shares without changing weights or selection.

## Acceptance evidence

Return the registry entry, focused test command/output, one audited monthly target showing source date and weights, one Notebook retrieval example, and allocation plans for NT$10,000,000 and a non-default capital amount. Report any shared-engine dependency as unavailable; do not bypass it.
