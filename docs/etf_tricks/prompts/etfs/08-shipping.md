# ETF Child Prompt 08 — Shipping and Transportation

Goal: `GOAL-ETF-TRICKS-001`

ETF ID: `shipping`

Use the shared universe and execution engine. Preserve the source's exact industry spelling.

## Contract

- Include exact `security_master.main_industry` values `M2600 Shipping and Transportation` and `OTC26 OTC Transporation`.
- The string `Transporation` is intentionally matched as stored; do not silently correct it before comparison.
- Apply fixed `liquidity_ratio_vs_ix0001_20d >= 0.05%`.
- Rank by complete ADV20 descending, then market cap descending and ticker ascending.
- Select at most ten, apply common one-to-four and zero-candidate carry rules, and use equal weights.

## Required tests

- Include both exact industry strings and reject fuzzy-name matches.
- Test values immediately below and at 0.05%.
- Require full ADV20.
- Prove equal weighting and stable ties.
- Prove no post-inception zero-holdings date under candidate shortage.

## Acceptance evidence

Return membership, aligned liquidity evidence, ADV20 ranking, selected target, focused tests, shortage/carry diagnostics, and arbitrary-capital allocation output.
