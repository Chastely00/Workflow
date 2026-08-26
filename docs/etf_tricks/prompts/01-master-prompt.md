# Master Prompt — Taiwan Equity ETF Tricks

Goal reference: `GOAL-ETF-TRICKS-001`

## Role and authority

You are the implementation and validation owner for the Taiwan-equity ETF Tricks subsystem in `C:\Users\ChastLai\Documents\量化交易Workflow`.

Before acting, read completely:

1. `AGENTS.md`
2. `docs/superpowers/specs/2026-08-26-etf-tricks-design.md`
3. `docs/etf_tricks/prompts/README.md`
4. `docs/superpowers/plans/2026-08-26-etf-tricks-implementation.md`
5. The ETF child prompt relevant to the current slice.

The approved design spec is the quantitative and accounting source of truth. This Master Prompt governs execution. A child prompt may narrow one ETF but may not override common rules. If code, schema, data, or prompts contradict a higher authority, stop that path, preserve evidence, and report the exact conflict.

## Required outcome

Build one importable `etf_tricks` package and one thin Notebook workflow that:

- Produces 13 Daily NAV curves and 13 Daily ETF traded-amount curves.
- Uses a shared event-driven accounting engine and 13 declarative ETF specifications.
- Exposes daily holdings, trades, monthly targets, complete candidate audits, and diagnostics.
- Allows Jupyter users to retrieve all 13 results with a small public API.
- Converts arbitrary ETF capital and existing positions into stock-level integer-share allocation and rebalance plans.
- Reports `READY` only after fresh full-history validation passes every hard gate.

NT$10,000,000 is the default validation notional and NAV starts at 100. The capital parameter must remain configurable everywhere.

## Non-negotiable scope

Implement only the ETF Trick data, selection, accounting, Notebook, allocation, and validation system. Do not implement Dollar bars, FFD, stationarity tests, ML features, training, predictions, or broker order submission. `for_ffd()` is an export adapter only.

Do not commit raw or derived market data, secrets, connection strings, or Notebook outputs. Preserve unrelated working-tree changes.

## Required architecture

Use these bounded responsibilities:

- `DataGateway`: manifest-first canonical DataAnalysts reads and schema/coverage checks.
- `TradingCalendar`: `TRADEDAY_TWSE` month ends and variable next-month date sets.
- `PITFeatureEngine`: all point-in-time signals and audit fields.
- `UniverseEngine`: listing, delisting, industry, liquidity, ranking, and weights.
- `ETFSpecRegistry`: exactly 13 declarative specs.
- `PortfolioExecutionEngine`: synthetic total return, integer trading, cash, fees, missing prices, and NAV.
- `ETFTrickResult`: long-form canonical tables plus wide Notebook views.
- `AllocationPlanner`: arbitrary-capital allocation and actual-position rebalancing.
- `ReadinessReport`: fail-closed validation with `目前可用` and `目前缺失／限制`.
- `ETFTrickLab`: the only main Notebook facade.

Never create 13 separate backtest engines or place core formulas in Notebook cells.

## Data contract

Read only manifest-declared DataAnalysts canonical artifacts. Validate `status == ready`, required schema, logical-key uniqueness, and requested date coverage before feature computation. Never search approximate folders or fall back to another store.

Use:

- `daily_price_volume`: raw `close`, verified `adj_close`, `volume`, `traded_value`, `turnover`, `market_cap`.
- `daily_chip`: `qfii_examt`, `fund_examt`, `dlrp_examt`.
- `monthly_sales`: `r18`, period and availability dates.
- `financial_statement_raw`: `r103`, TTM/consolidated/currency fields, period and availability dates.
- `security_master`: all historical securities, listing/delisting dates, current industry classification.
- `TRADEDAY_TWSE`: all formation and execution dates.
- `IX0001` source `amt`: liquidity denominator.

The base universe is TWSE plus TPEx common stocks with valid four-character non-zero tickers. `daily_tradability` is retained as an unused future interface.

## Common liquidity and selection

Define:

```text
liquidity_ratio_vs_ix0001_20d
  = sum_20d(stock traded_value) / sum_20d(IX0001 amt)
```

Use aligned complete 20-day calendar windows. For the 11 non-sector ETFs, use 0.2% when that pool contains at least five valid candidates; otherwise rebuild at 0.1%. Financial uses 0.1%; shipping uses 0.05%.

Select at most ten. Hold all if only one to nine are valid. If zero are valid after inception, retain prior actual holdings. Do not emit a zero-holdings ETF. Rank with stable keys: primary signal, ADV20 descending, market cap descending, ticker ascending.

Only market cap uses market-cap target weights. The other 12 use equal target weights.

## ETF registry

| ETF ID | Signal and unique rule | Direction |
|---|---|---|
| `market_cap` | Positive month-end market cap; market-cap weighted | Descending |
| `monthly_sales` | PIT `r18`; latest available period; age 0–2 months | Descending |
| `chip` | Complete 20-day sum of `qfii_examt + fund_examt + dlrp_examt` | Descending |
| `roe` | PIT `r103`, `TTM`, consolidated, NTD, age <=180 days, positive; exclude financial industries | Descending |
| `momentum` | `adj_close[T-21] / adj_close[T-252] - 1` | Descending |
| `low_volatility` | 60-date simple-return sample volatility, minimum 20 observations, `sqrt(252)` | Ascending |
| `financial` | Exact financial industry codes; rank ADV20 | Descending |
| `shipping` | Exact shipping industry codes; rank ADV20 | Descending |
| `volume_ratio` | `rolling(20).mean() / rolling(60).mean().shift(20)` with complete non-overlap | Descending |
| `traded_amount` | Complete ADV20 | Descending |
| `turnover` | Complete 20-day mean turnover | Descending |
| `sharpe_60d` | Complete 60-return mean/sample-std, RF=0, annualized | Descending |
| `sortino_60d` | Complete 60-return LPM2 downside deviation, MAR=0, annualized | Descending |

Use the child prompts for exact ETF-specific tests and evidence. Use the design spec for the complete formulas and exclusions.

## PIT timing

Form month `M+1` targets only after the final `TRADEDAY_TWSE` close in month `M`. Use only data available at that close. Revenue and financial rows require `source_available_date <= formation_date`. Never apply the new target to month `M` returns.

Execute across all actual trading dates in month `M+1`; `N` is read from the calendar and is never fixed at 20. On the first execution date, calculate raw-close integer target shares. Freeze the start-to-target share delta and use cumulative `round_half_away_from_zero(delta_q * k/N)` progress.

## Accounting and execution

- Return signals and synthetic total-return economics use `adj_close`.
- Trade shares, notional, affordability, costs, and raw holdings valuation use raw `close`.
- Before strategy trades, apply the approved synthetic corporate-action multiplier from relative adjusted/raw returns; convert fractional resulting shares to cash and audit both values.
- Strategy orders are integer and at least one share.
- Net same-day same-stock directions, sell before buy, and never allow negative cash, financing, short positions, or over-selling.
- When cash is insufficient, scale all buys proportionally, floor to shares, then allocate residual cash to the lowest schedule-completion ratio with ticker ascending as tie-break.
- Carry unfilled buys as backlog. Start the next month from actual holdings and cash.
- Do not allow an established ETF to become completely uninvested; defer a final sell if no target buy can execute.
- Commission per non-zero daily stock direction is `max(NT$1, round_half_up(notional * 0.1425%))`.
- Sell tax is `round_half_up(notional * 0.3%)`.
- Use `Decimal`; slippage is zero.
- Missing current price prohibits trading and uses the last valid price for valuation with a stale flag.
- On delisting, use the approved last-valid-close forced settlement and normal sell costs.

Compute post-trade assets as cash plus raw-close market value and set `NAV = 100 * assets / initial_capital`. The series begins at the first actual invested date.

Compute Daily ETF amount with same-day stock `traded_value` and previous-close actual economic weights. Cash contributes zero. Missing stock amount contributes zero with a quality flag. Never use current post-trade weights.

## Public Notebook API

The following workflow must work without exposing internal modules:

```python
from etf_tricks import ETFTrickLab

lab = ETFTrickLab.from_data_analysts()
result = lab.run_all(start_date=start_date, end_date=end_date, initial_capital=capital)

result.nav
result.returns
result.amount
result.holdings
result.trades
result.targets
result.candidates
result.diagnostics
result.for_ffd("momentum")
```

`for_ffd()` returns unique ordered columns `date, etf_id, nav, daily_return, etf_amount` and performs no FFD.

Allocation must accept arbitrary capital:

```python
lab.allocate(etf_id=etf_id, as_of_date=as_of_date, capital=capital)
lab.rebalance(
    etf_id=etf_id,
    as_of_date=as_of_date,
    current_positions=current_positions,
    current_cash=current_cash,
    capital_delta=capital_delta,
)
```

Return stock/name, weight, raw close, theoretical notional, actual integer shares/notional, costs, odd-lot difference, residual cash, net orders, and variable-`N` execution schedule. Return `infeasible_allocation` if no eligible share plus fee can be funded.

## Required outputs

Produce canonical long-form tables `daily_etf`, `daily_holdings`, `trades`, `monthly_targets`, `candidate_audit`, and `diagnostics`. Enforce the exact fields and unique keys in the approved design. Store result metadata with run configuration, manifest identities/hashes, and spec identity.

Notebook wide views are conveniences only; all audits and reconciliation use long-form tables.

## Required development sequence

Follow `docs/superpowers/plans/2026-08-26-etf-tricks-implementation.md` with TDD. Each slice must begin with a failing test, implement the smallest correct behavior, run focused tests, run the relevant regression set, and commit only scoped files. Do not skip ahead to full-history performance plots before the hand-checkable ledger passes.

Use the child prompts to implement or review each ETF spec against the shared engine. A child prompt cannot authorize duplicated readers, calendars, cost models, execution engines, or result schemas.

## Validation gates

Hard failures include missing/non-ready artifacts, missing schema or coverage, invalid calendar, absent IX0001 denominator, unresolved duplicate keys, PIT timing violations, negative cash/shares, over-selling, invalid or duplicate NAV, inability to establish any ETF, incomplete 13-ETF output, or broken asset reconciliation. A hard failure makes the final headline `NOT READY`.

Warnings that must remain visible include candidate count below five, zero-candidate carry-forward, stale prices, backlog, incomplete month-end transition, forced delisting, missing stock amount, snapshot industry classification, and synthetic corporate-action limitations.

Before claiming completion, run fresh tests and full-history execution through the public API. Report by ETF: inception, row count, final NAV, maximum stale days, candidate shortages, incomplete transitions, total costs, and artifact paths/hashes. Include explicit `目前可用` and `目前缺失／限制` sections. Do not infer alpha or out-of-sample profitability from successful curve production.

## Persistent handoff

At the end of every bounded slice, record:

- What is verified by fresh evidence.
- What remains unavailable or blocked.
- The exact next smallest blocking slice.
- Commands, result status, artifact paths, and commit identity.

Resume from this evidence on the next run. Do not restart with a new ad hoc implementation. Continue until every completion gate in `GOAL-ETF-TRICKS-001` is proven.
