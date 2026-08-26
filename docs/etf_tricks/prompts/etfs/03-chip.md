# ETF Child Prompt 03 — Institutional Chip

Goal: `GOAL-ETF-TRICKS-001`

ETF ID: `chip`

Use only the shared feature and execution paths defined by the authority documents.

## Contract

For each of the 20 aligned trading dates, require all three `daily_chip` values and calculate:

```text
chip_20d = sum_20d(qfii_examt + fund_examt + dlrp_examt)
```

Do not use `qfii_ex`, `fund_ex`, `dlrp_ex`, share counts, market-cap normalization, traded-amount normalization, or zero fill. Apply common universe, two-stage liquidity, descending ranking, tie-breaks, candidate rules, and equal target weights.

## Required tests

- Hand-calculate a 20-day three-component sum.
- One missing component on one day makes the stock ineligible.
- Negative daily flows remain valid and are summed, not clipped.
- Values after formation cannot affect the signal.
- Tie-breaks and equal weights are deterministic.

## Acceptance evidence

Return source-column verification, observation count, component sums, total signal, focused test output, one selected target, and explicit excluded-row evidence.
