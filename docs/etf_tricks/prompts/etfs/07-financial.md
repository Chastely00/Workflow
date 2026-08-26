# ETF Child Prompt 07 — Financial Sector

Goal: `GOAL-ETF-TRICKS-001`

ETF ID: `financial`

Use the shared universe and execution engine. Do not infer sector membership from ticker or company-name keywords.

## Contract

- Include exact `security_master.main_industry` values `M2800 Financial Industry` and `OTC28 OTC Banking`.
- Apply the fixed `liquidity_ratio_vs_ix0001_20d >= 0.1%` threshold; do not use the common 0.2% preferred tier.
- Rank eligible members by complete ADV20 descending, then market cap descending and ticker ascending.
- Select at most ten and use common candidate-shortage/carry rules.
- Use equal target weights; market cap is a tie-break only.

## Required tests

- Include both exact industries and exclude similar names/codes.
- Test eligibility immediately below and at 0.1%.
- Require complete ADV20.
- Prove descending ADV20 ranking and equal weights.
- Prove a zero-candidate month carries actual holdings.

## Acceptance evidence

Return the exact sector membership audit, liquidity numerator/denominator/ratio, ADV20 rank, selected target, focused tests, and an arbitrary-capital allocation plan.
