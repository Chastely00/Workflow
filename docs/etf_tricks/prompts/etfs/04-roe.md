# ETF Child Prompt 04 — ROE

Goal: `GOAL-ETF-TRICKS-001`

ETF ID: `roe`

Use the shared PIT and universe engines. ROE timing or report-version mistakes are hard failures.

## Contract

- Use `financial_statement_raw.r103` only.
- Require `no == "TTM"`, `merg == "Y"`, and `curr == "NTD"`.
- At formation date `T`, require `source_available_date <= T`; choose latest period and latest version available by `T`.
- Require `0 <= T - period_end_date <= 180` calendar days and finite `r103 > 0`.
- Do not fall back to annual, quarterly, or unconsolidated reports.
- Do not winsorize, cap, standardize, or silently remove extreme positive values; expose them in audit output.
- Exclude `M2800 Financial Industry` and `OTC28 OTC Banking`.
- Apply common two-stage liquidity, descending ranking, tie-breaks, candidate rules, and equal weights.

## Required tests

- Reject post-formation reports and revisions.
- Accept 180-day age and reject 181.
- Reject non-TTM, unconsolidated, non-NTD, zero, negative, and missing values.
- Exclude both exact financial industry strings.
- Preserve an extreme finite positive r103 and show its rank.

## Acceptance evidence

Return focused tests and a candidate audit with report period, availability, revision choice, age, report flags, industry, raw r103, and exclusion reason.
