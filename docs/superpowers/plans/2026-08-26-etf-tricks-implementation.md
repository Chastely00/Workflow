# Taiwan Equity ETF Tricks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a manifest-first, PIT-safe shared engine that produces 13 Taiwan-equity ETF Trick Daily NAV and amount curves and exposes arbitrary-capital Notebook allocation APIs.

**Architecture:** Add one focused `etf_tricks` Python package. A manifest-backed gateway feeds PIT features and a declarative 13-spec registry into one event-driven portfolio engine; `ETFTrickLab` is the Notebook facade, while `AllocationPlanner` reuses the same specifications and cost rules for arbitrary capital.

**Tech Stack:** Python 3.12, pandas, NumPy, PyArrow, pytest, project-local `.venv`.

**Spec:** `docs/superpowers/specs/2026-08-26-etf-tricks-design.md`

## Global Constraints

- Read `AGENTS.md` and the complete spec before changing code.
- Treat the spec as authoritative; stop and report any contradiction rather than inventing a fallback.
- Use DataAnalysts manifests and canonical Parquet only; no path guessing or implicit alternative stores.
- Use `adj_close` for return economics and raw `close` for shares, notional, costs, affordability, and valuation after synthetic corporate-action conversion.
- Preserve PIT availability dates and month-end-to-next-month execution timing.
- Do not use `daily_tradability` in this version.
- Do not implement Dollar bars, FFD, stationarity selection, ML features, or broker submission.
- Keep core logic out of Notebook cells.
- Do not modify or commit unrelated user files, raw data, derived datasets, or Notebook outputs.
- Use `.venv\Scripts\python.exe -m pytest` for verification.

---

## Planned File Structure

```text
etf_tricks/
  __init__.py              public exports only
  models.py                immutable configs, result and allocation types
  registry.py              the 13 declarative ETF specifications
  data_gateway.py          manifest-first DataAnalysts access
  calendar.py              monthly formation and execution calendars
  features.py              PIT-safe feature computations
  universe.py              listing, industry and liquidity eligibility
  costs.py                 Decimal fee and tax rules
  execution.py             synthetic CA and self-financing daily ledger
  allocation.py            arbitrary-capital allocation and rebalance plans
  result.py                canonical tables and Notebook convenience views
  validation.py            hard gates, reconciliation, readiness report
  lab.py                   ETFTrickLab facade
tests/etf_tricks/
  conftest.py
  test_registry.py
  test_data_gateway.py
  test_features.py
  test_universe.py
  test_costs.py
  test_execution.py
  test_allocation.py
  test_result.py
  test_validation.py
  test_integration.py
scripts/
  etf_tricks_quickstart.ipynb
```

## Task 1: Public contracts and 13-spec registry

**Files:**
- Create: `etf_tricks/__init__.py`
- Create: `etf_tricks/models.py`
- Create: `etf_tricks/registry.py`
- Test: `tests/etf_tricks/test_registry.py`

**Interfaces:**
- Produces: `ETFSpec`, `CostPolicy`, `RunConfig`, `ETF_IDS`, and `get_etf_spec(etf_id: str) -> ETFSpec`.
- Consumes: exact constants and definitions from design sections 2, 4, and 5.

- [ ] **Step 1: Write failing registry tests**

```python
from etf_tricks.registry import ETF_IDS, get_etf_spec


def test_registry_has_exactly_13_unique_ids():
    assert len(ETF_IDS) == 13
    assert len(set(ETF_IDS)) == 13


def test_only_market_cap_uses_market_cap_weighting():
    modes = {etf_id: get_etf_spec(etf_id).weighting for etf_id in ETF_IDS}
    assert modes["market_cap"] == "market_cap"
    assert {v for k, v in modes.items() if k != "market_cap"} == {"equal"}
```

- [ ] **Step 2: Run the tests and confirm import failure**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_registry.py -v`

Expected: FAIL because `etf_tricks.registry` does not exist.

- [ ] **Step 3: Implement frozen model types and all 13 specifications**

Define `ETFSpec` as a frozen dataclass with `etf_id`, `signal_name`, `direction`, `weighting`, `min_candidates`, `max_candidates`, `liquidity_policy`, and optional industry include/exclude values. Populate the exact IDs from the spec; do not encode formulas as arbitrary lambdas.

- [ ] **Step 4: Run registry tests**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_registry.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add etf_tricks/__init__.py etf_tricks/models.py etf_tricks/registry.py tests/etf_tricks/test_registry.py
git commit -m "feat: define ETF trick contracts and registry"
```

## Task 2: Manifest gateway and trading calendar

**Files:**
- Create: `etf_tricks/data_gateway.py`
- Create: `etf_tricks/calendar.py`
- Test: `tests/etf_tricks/test_data_gateway.py`

**Interfaces:**
- Produces: `DataGateway.from_data_analysts(root: Path)`, `read_artifact(artifact_id, columns, start, end) -> pd.DataFrame`, and `TradingCalendar.month(date) -> tuple[pd.Timestamp, ...]`.
- Consumes: DataAnalysts manifest JSON and canonical paths.

- [ ] **Step 1: Write tests for ready status, required columns, path containment, and unique dates**

Use temporary manifests and Parquet fixtures. Assert that a non-ready manifest, missing column, escaped path, duplicate calendar date, or absent requested coverage raises a typed `DataContractError` with artifact and field names.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_data_gateway.py -v`

Expected: FAIL because gateway types are undefined.

- [ ] **Step 3: Implement exact manifest-first reads**

Resolve only `artifact_paths` declared by the requested manifest. Reject paths outside the declared DataAnalysts store. Normalize dates without changing value meaning. Implement month-end formation and next-month date enumeration from `TRADEDAY_TWSE`.

- [ ] **Step 4: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_data_gateway.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add etf_tricks/data_gateway.py etf_tricks/calendar.py tests/etf_tricks/test_data_gateway.py
git commit -m "feat: add manifest-backed ETF data gateway"
```

## Task 3: PIT feature engine

**Files:**
- Create: `etf_tricks/features.py`
- Test: `tests/etf_tricks/test_features.py`

**Interfaces:**
- Produces: `PITFeatureEngine.compute(formation_date, panels) -> pd.DataFrame` with one row per ticker and all named signal and audit fields.
- Consumes: normalized frames from `DataGateway` and dates from `TradingCalendar`.

- [ ] **Step 1: Write exact formula and timing tests**

Cover 20-day chip sums, r18 availability and two-month staleness, TTM consolidated r103 availability and 180-day staleness, 12-1 momentum endpoints, low-volatility minimum 20 observations, non-overlapping volume ratio, ADV20, turnover20, full-60 Sharpe, and LPM2 Sortino.

- [ ] **Step 2: Add leakage tests**

Append an extreme post-formation monthly-sales and ROE record and assert the formation-date feature frame is byte-for-byte unchanged after stable sorting.

- [ ] **Step 3: Run the tests and confirm failure**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_features.py -v`

Expected: FAIL because `PITFeatureEngine` is undefined.

- [ ] **Step 4: Implement vectorized formulas with explicit observation counts**

Return both values and fields such as `*_observation_count`, chosen source period, source availability date, and exclusion reason. Never zero-fill a missing signal input.

- [ ] **Step 5: Run focused tests and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_features.py -v`

```powershell
git add etf_tricks/features.py tests/etf_tricks/test_features.py
git commit -m "feat: compute PIT-safe ETF signals"
```

## Task 4: Universe selection and target weights

**Files:**
- Create: `etf_tricks/universe.py`
- Test: `tests/etf_tricks/test_universe.py`

**Interfaces:**
- Produces: `UniverseEngine.select(spec, formation_date, features, security_master, ix0001) -> SelectionResult` containing all candidate reasons and selected targets.
- Consumes: `ETFSpec` and `PITFeatureEngine` output.

- [ ] **Step 1: Write tests for listing eligibility and exact industry codes**

Assert four-character non-zero tickers, listing/delisting date checks, financial and shipping exact strings, and ROE financial exclusions.

- [ ] **Step 2: Write liquidity and ranking tests**

Assert aligned 20-day stock/index sums, 0.2% preferred and 0.1% fallback pools, financial 0.1%, shipping 0.05%, one-to-four behavior, zero-candidate carry signal, stable sorting, and ticker tie-break.

- [ ] **Step 3: Run and confirm failure**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_universe.py -v`

- [ ] **Step 4: Implement selection and weight normalization**

Return equal target weights for 12 ETFs and normalized positive market-cap weights only for `market_cap`. Persist every considered candidate and exclusion reason.

- [ ] **Step 5: Run and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_universe.py -v`

```powershell
git add etf_tricks/universe.py tests/etf_tricks/test_universe.py
git commit -m "feat: select ETF universes and targets"
```

## Task 5: Costs and event-driven execution ledger

**Files:**
- Create: `etf_tricks/costs.py`
- Create: `etf_tricks/execution.py`
- Create: `tests/etf_tricks/conftest.py`
- Test: `tests/etf_tricks/test_costs.py`
- Test: `tests/etf_tricks/test_execution.py`

**Interfaces:**
- Produces: `transaction_cost(side, shares, close, policy) -> CostBreakdown` and `PortfolioExecutionEngine.run(spec, targets, market, calendar, initial_capital) -> EngineTables`.
- Consumes: monthly `SelectionResult` targets and raw/adjusted daily prices.

- [ ] **Step 1: Write Decimal rounding tests**

Test no-trade zero fee, one-dollar minimum, half-up rounding, 0.1425% commission, 0.3% sell tax, and same-day netting.

- [ ] **Step 2: Write a hand-checkable two-month ledger fixture**

Use three to five stocks and three-day/five-day months. Assert every day's scheduled shares, executed shares, cash, costs, raw market value, synthetic multiplier, synthetic cash, backlog, NAV, and target completion ratio.

- [ ] **Step 3: Add safety cases**

Test sell-before-buy, proportional buy scaling, lowest-completion residual allocation, ticker tie-break, negative-cash prevention, minimum one-share orders, no-empty-post-inception protection, stale prices, and forced delisting.

- [ ] **Step 4: Run and confirm failure**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_costs.py tests/etf_tricks/test_execution.py -v`

- [ ] **Step 5: Implement minimal deterministic ledger**

Use the exact cumulative schedule and synthetic corporate-action formulas from the spec. Keep integer strategy shares and explicit cash. Raise typed invariant errors rather than clipping negative balances.

- [ ] **Step 6: Run and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_costs.py tests/etf_tricks/test_execution.py -v`

```powershell
git add etf_tricks/costs.py etf_tricks/execution.py tests/etf_tricks/conftest.py tests/etf_tricks/test_costs.py tests/etf_tricks/test_execution.py
git commit -m "feat: execute self-financing ETF portfolios"
```

## Task 6: Results, ETF amount, and Notebook facade

**Files:**
- Create: `etf_tricks/result.py`
- Create: `etf_tricks/lab.py`
- Test: `tests/etf_tricks/test_result.py`

**Interfaces:**
- Produces: `ETFTrickResult`, `ETFTrickLab.from_data_analysts()`, `run_all(start_date, end_date, initial_capital)`, and `for_ffd(etf_id)`.
- Consumes: gateway, feature, universe, and execution components.

- [ ] **Step 1: Write result-schema tests**

Assert long-form unique keys, 13-column wide NAV/return/amount views, and exact `for_ffd()` columns: `date`, `etf_id`, `nav`, `daily_return`, `etf_amount`.

- [ ] **Step 2: Write ETF amount timing test**

Construct a weight change at date `t` and assert `ETF_amount[t]` uses date `t-1` actual weights, cash contributes zero, and a missing stock traded value contributes zero with a flag.

- [ ] **Step 3: Run and confirm failure**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_result.py -v`

- [ ] **Step 4: Implement canonical tables and facade**

Expose pandas conveniences without changing the underlying long-form tables. Ensure run configuration, manifest identities, and specification hashes are included in result metadata.

- [ ] **Step 5: Run and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_result.py -v`

```powershell
git add etf_tricks/result.py etf_tricks/lab.py tests/etf_tricks/test_result.py
git commit -m "feat: expose ETF Trick Notebook results"
```

## Task 7: Arbitrary-capital allocation and rebalance API

**Files:**
- Create: `etf_tricks/allocation.py`
- Modify: `etf_tricks/lab.py`
- Test: `tests/etf_tricks/test_allocation.py`

**Interfaces:**
- Produces: `ETFTrickLab.allocate(etf_id, as_of_date, capital) -> AllocationPlan` and `rebalance(etf_id, as_of_date, current_positions, current_cash, capital_delta) -> AllocationPlan`.
- Consumes: the same registry, targets, trading calendar, costs, and integer affordability rules as the historical engine.

- [ ] **Step 1: Write zero-position and existing-position tests**

Assert arbitrary capital changes integer target shares, current holdings produce net orders, costs and residual cash reconcile, and NT$10,000,000 has no privileged code path.

- [ ] **Step 2: Write infeasible and schedule tests**

Assert capital unable to buy one eligible share plus commission returns `infeasible_allocation`, and a valid plan exposes both full target basket and variable-`N` daily schedule.

- [ ] **Step 3: Run and confirm failure**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_allocation.py -v`

- [ ] **Step 4: Implement allocation using shared execution rules**

Return stock name, weight, raw close, theoretical and actual notional, integer shares, fees/tax, odd-lot difference, residual cash, and per-date scheduled shares.

- [ ] **Step 5: Run and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_allocation.py -v`

```powershell
git add etf_tricks/allocation.py etf_tricks/lab.py tests/etf_tricks/test_allocation.py
git commit -m "feat: plan arbitrary-capital ETF allocations"
```

## Task 8: Fail-closed validation and readiness reporting

**Files:**
- Create: `etf_tricks/validation.py`
- Test: `tests/etf_tricks/test_validation.py`

**Interfaces:**
- Produces: `validate_result(result, calendar, expected_etf_ids) -> ReadinessReport` with status `READY` or `NOT_READY`, hard failures, warnings, `目前可用`, and `目前缺失／限制`.
- Consumes: all canonical output tables.

- [ ] **Step 1: Write one failing case per hard gate**

Cover missing ETF, missing date after inception, duplicate keys, non-finite/non-positive NAV, negative cash/shares, broken accounting identity, PIT violation, and absent IX0001 liquidity evidence.

- [ ] **Step 2: Write warning-only cases**

Cover low candidate counts, carry-forward, stale prices, backlog, incomplete transition, forced delisting, missing traded amount, snapshot industries, and synthetic corporate actions.

- [ ] **Step 3: Run and confirm failure**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_validation.py -v`

- [ ] **Step 4: Implement readiness aggregation**

Never convert hard failures to warnings. Include inception date, rows, final NAV, maximum staleness, candidate shortage, incomplete transition, and total costs by ETF.

- [ ] **Step 5: Run and commit**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_validation.py -v`

```powershell
git add etf_tricks/validation.py tests/etf_tricks/test_validation.py
git commit -m "feat: validate ETF Trick readiness"
```

## Task 9: Integrated 13-ETF run and thin Notebook

**Files:**
- Create: `tests/etf_tricks/test_integration.py`
- Create: `scripts/etf_tricks_quickstart.ipynb`
- Modify: `README.md`

**Interfaces:**
- Consumes: `ETFTrickLab`, `ETFTrickResult`, and `ReadinessReport`.
- Produces: one reproducible Notebook entrypoint and bounded integration evidence.

- [ ] **Step 1: Write bounded 13-ETF integration test**

Use governed synthetic fixtures or a small declared canonical interval. Assert exactly 13 IDs, continuous post-inception calendar rows, unique schemas, accounting reconciliation, and working `allocate()` for a non-default capital amount.

- [ ] **Step 2: Run and confirm any missing integration wiring**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks/test_integration.py -v`

- [ ] **Step 3: Complete integration wiring and rerun all ETF tests**

Run: `.venv\Scripts\python.exe -m pytest tests/etf_tricks -v`

Expected: PASS with no warnings converted from hard failures.

- [ ] **Step 4: Create a thin output-cleared Notebook**

The Notebook may contain imports, configuration, `run_all`, inspection of `nav` and `amount`, `for_ffd`, and one arbitrary-capital `allocate` example. It must not duplicate formulas or retain execution outputs in Git.

- [ ] **Step 5: Document the API and commit**

```powershell
git add tests/etf_tricks/test_integration.py scripts/etf_tricks_quickstart.ipynb README.md
git commit -m "docs: add ETF Trick Notebook workflow"
```

## Task 10: Full-history validation artifact

**Files:**
- Create: `docs/validation/etf-tricks-readiness.md`
- Create: governed output metadata paths selected by existing repository conventions; do not commit data Parquet unless explicitly authorized.

**Interfaces:**
- Consumes: the completed engine and DataAnalysts canonical manifests.
- Produces: final readiness evidence for the 13 Daily NAV and amount curves.

- [ ] **Step 1: Run environment and focused test verification**

Run:

```powershell
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m pytest tests/etf_tricks -v
```

- [ ] **Step 2: Run the full manifest-backed history through the public API**

Use `ETFTrickLab.from_data_analysts()` and the common validated date range. Persist run configuration, manifest hashes, specification hash, output row counts, and artifact locations.

- [ ] **Step 3: Run final reconciliation**

Require all 13 curves and amounts, continuous post-inception calendar coverage, finite NAV, unique keys, non-negative cash/shares, and exact accounting identities.

- [ ] **Step 4: Write the readiness report**

Include per-ETF inception, rows, final NAV, maximum staleness, candidate shortages, incomplete transitions, total cost, and explicit `目前可用` and `目前缺失／限制` sections. If any hard gate fails, headline status must be `NOT READY`.

- [ ] **Step 5: Commit only code, tests, documentation, and non-sensitive metadata**

```powershell
git add docs/validation/etf-tricks-readiness.md
git commit -m "docs: report ETF Trick readiness"
```

Do not commit raw or derived market data.
