# Taiwan Equity ETF Tricks Design

Date: 2026-08-26

Status: Approved in chat; pending written-spec review

Scope: Design and prompt contract only. No implementation is authorized by this document.

## 1. Objective

Build one shared, point-in-time-safe engine that produces 13 synthetic Taiwan-equity ETF Tricks from the DataAnalysts canonical store. Each ETF must expose a continuous Daily NAV series from its own inception date, a Daily ETF traded-amount series, daily holdings, trades, monthly targets, candidate audits, and diagnostics. The universe includes TWSE-listed and TPEx-listed common stocks.

The same ETF definitions must also support a Notebook-facing allocation API that converts any supplied capital amount and current portfolio into target stock quantities and an executable monthly schedule. NT$10,000,000 is the default validation notional, not a hard-coded portfolio size.

The current scope ends after the 13 Daily NAV and ETF-amount curves are validated. Dollar bars, FFD, stationarity selection, and ML features are future work.

## 2. Required ETF Tricks

| ID | Name | Primary selection signal | Direction | Target weight |
|---|---|---|---|---|
| `market_cap` | 市值 | Month-end `market_cap` | High to low | Market-cap weight |
| `monthly_sales` | 月營收 | PIT-safe `r18` | High to low | Equal weight |
| `chip` | 籌碼 | 20-day sum of `qfii_examt + fund_examt + dlrp_examt` | High to low | Equal weight |
| `roe` | ROE | PIT-safe TTM consolidated `r103` | High to low | Equal weight |
| `momentum` | 動能 | 12-1 month momentum | High to low | Equal weight |
| `low_volatility` | 低波 | 60-day annualized volatility | Low to high | Equal weight |
| `financial` | 金融 | Financial industry, ranked by ADV20 | High to low | Equal weight |
| `shipping` | 航運 | Shipping industry, ranked by ADV20 | High to low | Equal weight |
| `volume_ratio` | 量能 | Non-overlapping 20/60-day volume ratio | High to low | Equal weight |
| `traded_amount` | 金額 | ADV20 | High to low | Equal weight |
| `turnover` | 週轉率 | 20-day mean turnover | High to low | Equal weight |
| `sharpe_60d` | 近60日 Sharpe | 60-day annualized Sharpe | High to low | Equal weight |
| `sortino_60d` | 近60日 Sortino | 60-day annualized Sortino | High to low | Equal weight |

Only `market_cap` uses market-cap target weights. The other 12 ETFs are equal weighted.

## 3. Data Contracts

### 3.1 Manifest-first access

All data access must start from DataAnalysts manifests and canonical Parquet paths. The engine must validate artifact status, schema, logical keys, and requested date coverage before computation. It must not search for similar paths, silently fall back to another store, or silently drop malformed rows.

Required inputs include:

- `daily_price_volume`: `date`, `ticker`, raw `close`, `adj_close`, `volume`, `traded_value`, `turnover`, and `market_cap`.
- `daily_chip`: `qfii_examt`, `fund_examt`, and `dlrp_examt`.
- `monthly_sales`: `r18`, `source_period_date`, and `source_available_date`.
- `financial_statement_raw`: `r103`, `no`, `merg`, `curr`, `period_end_date`, `source_available_date`, and revision fields.
- `security_master`: ticker, listing and delisting dates, `main_industry`, and stock name.
- `TRADEDAY_TWSE`: the canonical Taiwan trading calendar.
- `IX0001`: broad-market `amt`, mapped to the canonical traded-value field where applicable.

`daily_tradability` remains an optional future interface and is not used in this version.

### 3.2 Price roles

- `adj_close` is used for return-derived signals and synthetic total-return economics.
- Raw `close` is used for target-share calculation, integer orders, transaction notional, fees, taxes, cash affordability, and end-of-day holdings valuation after the synthetic corporate-action conversion.
- No code path may use `adj_close` as a tradable execution price.

### 3.3 Security master limitation

`security_master` is a snapshot rather than a complete PIT industry history. The current snapshot classification is accepted for this version because historical industry changes are expected to be limited. This must remain a disclosed limitation, not be described as fully PIT-safe industry history.

## 4. Common Universe and Liquidity

### 4.1 Base universe

The base universe consists of TWSE and TPEx common stocks represented by valid four-character, non-zero stock tickers. A stock is eligible on date `T` only when its `list_date <= T` and its `delist_date` is null or later than `T`. New stocks require a valid raw close before they may enter.

### 4.2 Liquidity metric

For a stock `i` at formation date `T`:

```text
liquidity_ratio_vs_ix0001_20d(i,T)
    = sum(last 20 valid stock traded_value)
      / sum(last 20 IX0001 amt)
```

The numerator and denominator use the same 20 `TRADEDAY_TWSE` dates and require a complete 20-day window. The metric must not be called total-market share because IX0001 is a TWSE-scale benchmark while the stock universe includes TWSE and TPEx.

### 4.3 Thresholds

For the 11 non-sector ETFs:

1. Build the preferred pool at `ratio >= 0.2%`.
2. If that pool has at least five valid candidates, rank within it and select at most ten.
3. If it has fewer than five, rebuild the pool at `ratio >= 0.1%`, rerank, and select at most ten.

Financial uses a fixed `0.1%` threshold. Shipping uses a fixed `0.05%` threshold.

If the final eligible pool contains five to ten stocks, hold all selected stocks up to ten. If it contains one to four, hold all available candidates. If it contains zero, retain the previous actual holdings and do not form a zero portfolio. ETF inception begins only when at least one stock can actually be purchased.

## 5. Signal Definitions

### 5.1 Market cap

Use finite, positive `daily_price_volume.market_cap` on the month-end formation date. Rank descending. For selected set `S`:

```text
target_weight(i) = market_cap(i) / sum(market_cap(j) for j in S)
```

Do not impose a single-stock cap and do not substitute free-float market cap.

### 5.2 Monthly sales

Use `monthly_sales.r18`, the trailing-12-month cumulative sales-growth rate.

- At formation date `T`, only rows with `source_available_date <= T` are visible.
- For each ticker, choose the latest `source_period_date`; for the same period, choose the latest version available by `T`, with a stable source-row tie-break.
- Define period age as `12*(year(T)-year(P)) + month(T)-month(P)` where `P` is `source_period_date`.
- Require `0 <= period_age <= 2`.
- Require finite `r18`; do not zero-fill.
- Rank descending.

### 5.3 Chip

For each of the last 20 trading days, require all three fields `qfii_examt`, `fund_examt`, and `dlrp_examt`. Define:

```text
chip_20d = sum_20d(qfii_examt + fund_examt + dlrp_examt)
```

Require a complete 20-day window; any missing component makes the stock ineligible. Do not normalize by market cap or traded amount. Rank descending.

### 5.4 ROE

Use `financial_statement_raw.r103` with:

- `no == "TTM"`
- `merg == "Y"`
- `curr == "NTD"`
- `source_available_date <= formation_date`
- latest `period_end_date`, then latest version available by the formation date
- `0 <= formation_date - period_end_date <= 180 calendar days`
- finite `r103 > 0`

Do not fall back to annual, quarterly, or unconsolidated figures. Do not winsorize or cap ROE. Exclude:

- `M2800 Financial Industry`
- `OTC28 OTC Banking`

Rank descending and expose raw `r103` in audits.

### 5.5 Momentum

Use raw trading-calendar offsets on `adj_close`:

```text
momentum_12_1(T) = adj_close[T-21] / adj_close[T-252] - 1
```

Both endpoints must exist and be finite and positive. The latest 21 trading days are excluded. Do not bridge missing-price gaps by substituting another date. Rank descending.

### 5.6 Low volatility

Use simple daily `adj_close` returns over the last 60 trading-calendar dates. A return is valid only when both adjacent trading-day prices exist. Require at least 20 valid observations:

```text
vol_60d = sample_std(valid_returns, ddof=1) * sqrt(252)
```

Require finite positive volatility. Rank ascending.

### 5.7 Financial and shipping

Use exact `security_master.main_industry` values:

- Financial: `M2800 Financial Industry`, `OTC28 OTC Banking`
- Shipping: `M2600 Shipping and Transportation`, `OTC26 OTC Transporation`

The source spelling `Transporation` must be preserved for matching. After the sector liquidity filter, rank by ADV20 descending.

### 5.8 Volume ratio

Use non-overlapping raw-volume windows:

```text
volume_ratio(T)
    = mean(volume[T-19:T])
      / mean(volume[T-79:T-20])
```

Equivalent implementation: `volume.rolling(20).mean() / volume.rolling(60).mean().shift(20)`. Require all 20 numerator and 60 denominator observations across the 80-day span, and require a positive denominator. Do not zero-fill. Rank descending.

### 5.9 Traded amount

```text
ADV20(T) = mean(last 20 traded_value observations)
```

Require a complete 20-day window. Rank descending.

### 5.10 Turnover

```text
turnover_20d(T) = mean(last 20 turnover observations)
```

Require 20 finite observations; do not zero-fill. Rank descending.

### 5.11 Sharpe

Using 60 complete simple daily `adj_close` returns and zero daily risk-free rate:

```text
sharpe_60d = mean(r) / sample_std(r, ddof=1) * sqrt(252)
```

Require all 60 returns and a finite positive denominator. Rank descending.

### 5.12 Sortino

With `MAR_d = 0` and 60 complete simple daily `adj_close` returns:

```text
downside_deviation = sqrt(mean(min(r - MAR_d, 0)^2 over all 60 days))
sortino_60d = (mean(r) - MAR_d) / downside_deviation * sqrt(252)
```

Positive-return days contribute zero to the downside moment. Require a finite positive downside deviation. Rank descending.

### 5.13 Stable ranking

Use stable sorting with the following keys:

1. Primary ETF signal in its declared direction.
2. ADV20 descending.
3. Market cap descending.
4. Ticker string ascending.

Persist all ranking keys in `candidate_audit` and `monthly_targets`.

## 6. Monthly Formation and Execution

### 6.1 Formation timing

Let `T_M` be the final `TRADEDAY_TWSE` date in month `M`. Form targets only after the `T_M` close, using information available by that close. Tag the target for month `M+1`; never apply it to month `M` returns.

### 6.2 Variable transition length

Let `D_(M+1) = {d_1, ..., d_N}` be all `TRADEDAY_TWSE` dates in the next month. `N` is the actual number of dates in the calendar, never a fixed 20.

On `d_1`, calculate integer target shares using raw close, actual pre-trade assets, target weights, and floor-to-affordable-share logic. Fees are not financed. The self-financing execution rules may reduce buys and create backlog.

For each stock, freeze the start-to-target share difference for the transition month:

```text
delta_q = target_q - start_q
scheduled_q(k)
    = start_q + round_half_away_from_zero(delta_q * k / N)
```

The daily order is the difference between cumulative scheduled shares and actual shares after synthetic corporate-action adjustment. Do not recalculate equal-weight targets every day from current prices; this would create unintended continuous rebalancing and repeated minimum fees.

### 6.3 Self-financing execution

- No short positions, financing, or negative cash.
- Net same-day same-stock orders to one direction.
- Execute sells before buys.
- If all buys are affordable, execute all integer orders.
- Otherwise scale desired buy quantities by a common affordability ratio and floor to integers.
- Allocate remaining cash one share at a time to the stock with the lowest cumulative schedule-completion ratio; break ties by ticker ascending.
- Recheck price plus final commission after each residual share.
- Carry unfilled quantities as backlog.
- At the next monthly rebalance, start from actual holdings and cash, not theoretical targets.
- If proposed trades would leave an established ETF with no shares and no target buy can execute, defer enough selling to retain at least one share. This enforces the approved no-empty-holdings requirement.

### 6.4 Transaction costs

For each date, stock, and net direction:

```text
notional = abs(executed_shares) * raw_close
commission = max(1, round_half_up(notional * 0.001425))
sell_tax = round_half_up(notional * 0.003)
```

Use `Decimal` and integer New Taiwan dollars. No trade means zero commission. Buy tax is zero. Slippage is zero in this version.

## 7. Synthetic Total-Return Accounting

The historical corporate-action event artifacts are not currently complete enough for a broker-exact raw-share ledger. The approved approach is a synthetic total-return ledger driven by the verified `adj_close`, while all strategy trades remain raw-close integer transactions.

Before strategy trading on date `t`, calculate:

```text
m(i,t) = (adj_close[i,t] / adj_close[i,t-1])
         / (close[i,t] / close[i,t-1])

q_exact = q[i,t-1] * m(i,t)
q_pretrade = floor(q_exact)
synthetic_ca_cash = (q_exact - q_pretrade) * close[i,t]
```

Add `synthetic_ca_cash` to cash. Treat the integer share change created by this operation as a synthetic corporate-action adjustment, not a strategy trade; charge no commission or tax. Apply the same multiplier to outstanding target and backlog share coordinates before deriving the day's strategy order. Persist the multiplier and cash conversion for audit.

The ratios above use the immediately preceding valid observation for that stock. If date `t` has no valid raw and adjusted close, do not apply a multiplier on `t`; carry the last valid valuation under the stale-price rule. When valid prices resume, compute the multiplier from the last valid paired raw/adjusted observation to the resumed observation. Signal windows remain subject to their stricter no-gap rules.

This convention models a total-return reinvestment process and preserves integer strategy orders, but it must not be described as a broker-exact reconstruction of dividends, splits, capital reductions, or fractional-entitlement handling.

## 8. Daily Event Order and NAV

For each ETF and trading day:

1. Start with prior-close holdings and cash.
2. Apply the synthetic corporate-action conversion.
3. Value pre-trade assets at current raw close.
4. Derive the cumulative scheduled order and backlog.
5. Net orders, execute sells, deduct sell commission and tax.
6. Allocate affordable buys, deduct buy commission, and update backlog.
7. Value post-trade assets:

```text
total_assets[t] = cash[t] + sum(shares[i,t] * raw_close[i,t])
NAV[t] = 100 * total_assets[t] / initial_capital
daily_return[t] = NAV[t] / NAV[t-1] - 1
```

The NAV series begins on the first date at which at least one share is successfully held. A stock bought at date `t` close receives no earlier part of date `t` return. Transaction costs reduce NAV immediately.

## 9. Missing Prices and Delisting

- If current raw close is not finite and positive, prohibit trading that stock and retain the order as backlog.
- For valuation only, carry the most recent valid raw close and increment `stale_price_days`; do not mutate source data.
- A missing current `traded_value` contributes zero to ETF amount and raises a data-quality flag.
- A stock with no prior valid price cannot be bought or valued.
- On `security_master.delist_date`, cash-settle remaining shares at the most recent valid raw close, charge normal sell commission and 0.3% tax, and mark `forced_delist_liquidation`.
- Report maximum staleness and every forced liquidation.

## 10. ETF Daily Traded Amount

Use previous-close actual economic weights:

```text
ETF_amount[t]
    = sum(stock_traded_value[i,t] * actual_weight[i,t-1])
```

Cash contributes zero. Use actual weights during gradual rebalancing, not target weights. Do not use current-close post-trade weights because that would introduce contemporaneous information into the metric.

## 11. Architecture

### 11.1 Components

- `DataGateway`: manifest-first reads and schema/coverage validation.
- `PITFeatureEngine`: PIT panels and all signal calculations.
- `UniverseEngine`: listing eligibility, industry filters, and liquidity gates.
- `ETFSpecRegistry`: 13 declarative ETF specifications.
- `PortfolioExecutionEngine`: monthly schedules, synthetic corporate actions, integer trading, cash, costs, missing prices, and NAV.
- `ETFTrickResult`: canonical outputs and Notebook conveniences.
- `AllocationPlanner`: arbitrary-capital target baskets, rebalances, and schedules.
- `ETFTrickLab`: the single high-level Notebook facade.

The core research and accounting logic must live in importable Python modules, not Notebook cells. A Notebook is a thin, reproducible consumer.

### 11.2 Notebook API

```python
from etf_tricks import ETFTrickLab

lab = ETFTrickLab.from_data_analysts()

result = lab.run_all(
    start_date="2005-01-01",
    end_date="2026-07-07",
    initial_capital=10_000_000,
)

nav = result.nav
amount = result.amount
ffd_input = result.for_ffd("momentum")
```

`initial_capital` is required to be configurable. The default validation value may be NT$10,000,000.

### 11.3 Allocation API

```python
plan = lab.allocate(
    etf_id="momentum",
    as_of_date="2026-07-31",
    capital=25_000_000,
)

rebalance = lab.rebalance(
    etf_id="momentum",
    as_of_date="2026-07-31",
    current_positions=current_positions,
    current_cash=current_cash,
    capital_delta=5_000_000,
)
```

`allocate()` must expose both the full target basket and the `TRADEDAY_TWSE`-based gradual schedule. `rebalance()` must return net integer orders from actual positions. If available capital cannot purchase one share of any eligible constituent after fees, return `infeasible_allocation`; do not fabricate fractional strategy holdings.

## 12. Output Schemas

Canonical storage is long-form Parquet; Notebook conveniences may pivot to pandas wide frames.

### 12.1 `daily_etf`

Unique key: `(date, etf_id)`.

Required fields include `nav`, `daily_return`, `total_assets`, `cash`, `invested_weight`, `cash_weight`, `etf_amount`, `holdings_count`, `commission`, `tax`, `total_cost`, `target_completion_ratio`, `stale_holding_count`, and `has_data_quality_flag`.

### 12.2 `daily_holdings`

Required fields include `date`, `etf_id`, `ticker`, `shares`, `raw_close`, `market_value`, `actual_weight`, `target_weight`, `synthetic_ca_multiplier`, `synthetic_ca_cash`, `stale_price_days`, and `source_price_date`.

### 12.3 `trades`

Required fields include `date`, `etf_id`, `ticker`, `side`, `scheduled_shares`, `backlog_before`, `executed_shares`, `unfilled_shares`, `raw_close`, `notional`, `commission`, `tax`, `cash_after`, and `is_forced_delist_liquidation`.

### 12.4 `monthly_targets`

Required fields include `formation_date`, `target_month`, `etf_id`, `ticker`, `rank`, `signal_name`, `signal_value`, `target_weight`, liquidity ratio, ADV20, market cap, source period and availability dates, and all tie-break columns.

### 12.5 `candidate_audit`

Persist every considered candidate and explicit inclusion/exclusion reasons, not only selected constituents.

### 12.6 `diagnostics`

Persist artifact checks, PIT checks, coverage, missing prices, forced liquidation, cash/backlog states, inception dates, and final 13-curve validation.

### 12.7 Notebook conveniences

`ETFTrickResult` exposes `nav`, `returns`, `amount`, `daily`, `holdings`, `trades`, `targets`, `candidates`, and `diagnostics`.

`result.for_ffd(etf_id)` returns a unique, date-sorted table with `date`, `etf_id`, `nav`, `daily_return`, and `etf_amount`. It does not perform FFD.

`AllocationPlan` includes ETF/date/ticker and stock name, target weight, raw close, supplied capital, theoretical target notional, integer target shares, actual allocated notional, estimated commission and sell tax where applicable, unallocated odd-lot difference, residual cash, execution date, and scheduled shares.

## 13. Fail-Closed Rules

The complete run must stop and report `NOT READY` when any of the following occurs:

- Required manifest missing, not ready, schema-incompatible, or without necessary coverage.
- Trading calendar invalid or non-unique.
- IX0001 amount unavailable for liquidity calculation.
- Price logical keys are duplicated without a governed unique resolution.
- Monthly-sales or ROE data violates availability timing.
- A trade produces negative cash, negative shares, or an over-sale.
- NAV is non-finite or non-positive, or output keys duplicate.
- Any ETF cannot establish an initial invested portfolio.
- The final output does not contain all 13 Daily NAV curves.

The following conditions may continue only with explicit diagnostics: candidate count below five, zero-candidate carry-forward, stale valuation, backlog, month-end completion below 100%, forced delisting, missing traded amount, snapshot industry limitation, and synthetic corporate-action accounting.

## 14. Testing and Validation

### 14.1 Unit tests

Test window boundaries, missing observations, 12-1 momentum exclusion, PIT selection and revisions, liquidity fallback, sector thresholds, all ranking directions, stable tie-breaks, equal and market-cap weights, variable monthly trading-day counts, share scheduling, all rounding rules, minimum commission, taxes, same-stock netting, sell-before-buy, proportional cash allocation, backlog, synthetic corporate actions, prior-weight ETF amount, missing prices, and forced delisting.

### 14.2 Hand-checkable fixture

Create a three-to-five-stock synthetic fixture spanning two months with deliberately short three-day and five-day trading calendars. Include a buy, sell, minimum fee, missing-price day, and synthetic corporate action. Manually specify the expected shares, cash, costs, NAV, weights, and ETF amount for every day.

### 14.3 Leakage tests

- Adding an extreme sales or ROE record after formation must not change the existing target.
- Changing month `M+1` prices must not change month `M` selection.
- Current post-trade weights must not enter current ETF amount.
- Input row shuffling must not change any selected constituent or result.

### 14.4 Integration and full-history validation

First run a bounded period for all 13 ETFs, reconcile every table, and smoke-test the Notebook API and arbitrary-capital allocation. Then run the common manifest-backed history and report, for every ETF, inception date, row count, final NAV, maximum stale-price days, candidate-shortage count, incomplete-transition count, and total cost.

Final acceptance requires:

- 13 unique ETF IDs.
- One finite Daily NAV row for every `TRADEDAY_TWSE` date from each ETF's inception through the requested end date.
- Reconciliation of `total_assets = cash + sum(market_value)`.
- Reconciliation among NAV, costs, trades, holdings, targets, and diagnostics.
- Explicit sections titled `目前可用` and `目前缺失／限制`.

This validation establishes data and accounting readiness only. It does not establish alpha, economic profitability, or out-of-sample performance.

## 15. Prompt Deliverables

After this written specification is approved, produce:

1. One Master Prompt covering the shared architecture, manifests, PIT rules, common engine, APIs, output contracts, tests, staged execution, and final fail-closed validation.
2. Thirteen child prompts, one per ETF, each referencing the shared contract and defining only its unique signal, eligibility, ranking, weighting, targeted tests, and acceptance evidence.

The prompts must instruct future implementers to preserve existing user files, keep Notebook cells thin, avoid ad hoc runner duplication, and report actual artifacts and fresh verification rather than code-only completion claims.

## 16. Explicitly Out of Scope

- Dollar-bar threshold design or construction.
- FFD calculation or selection of `d*`.
- ADF, CADF, or SADF implementation.
- ML feature generation, training, labeling, validation, or performance claims.
- VPIN, Kyle's lambda, ATR, ADX, skewness, kurtosis, VIX, or market-volatility features.
- Broker integration or live order submission.
- Full historical corporate-action reconstruction.

Future FFD work must distinguish a descriptive full-history transform from model evaluation. Selecting `d*` on the complete history and then evaluating ML on that same history would leak future information; any predictive study must choose `d*` within training-only or expanding-window boundaries.
