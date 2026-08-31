# ETF Tricks Market State Rebuild Implementation Plan

> **Superseded:** 2026-08-31 起不得再執行本文件。本文件依賴已取消的
> `official_market_status` 與可發布的 `DELISTED` row；新的 TEJ-only 權威計畫為
> `docs/superpowers/plans/2026-08-31-tej-only-etf-market-state-rebuild.md`。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consume certified DataAnalysts `daily_market_state`, rebuild all 13 ETF Tricks with authoritative Daily ETF amount and halt-safe execution, then run one explicitly authorized full-history AFML acceptance.

**Architecture:** Extend the manifest-backed gateway and execution tensors with a separate market-state channel. Portfolio valuation still uses last-valid raw close, while formation and execution require safely available `TRADING`; ETF amount joins previous-session holdings to `authoritative_traded_value` and treats only `ZERO_AUTHORIZED` as zero. Write new versioned outputs instead of replacing v5.

**Tech Stack:** Python 3.12, pandas, NumPy, PyArrow dataset scanning, pytest, existing `etf_tricks` and `etf_tricks.afml` packages.

**Spec:** `C:\Users\ChastLai\Documents\量化交易積木\.worktrees\daily-market-state\docs\superpowers\specs\2026-08-28-daily-market-state-design.md`

## Global Constraints

- Start only after canonical `official_market_status` and `daily_market_state` manifests pass scoped verify and store audit.
- Use `.venv\Scripts\python.exe`; do not install packages.
- Use `C:\Users\ChastLai\Documents\量化交易積木\DataAnalysts`, never Workflow's stale embedded copy.
- Do not change signals, ETF definitions, monthly schedule, raw-close execution, NAV accounting, costs, or corporate-action logic except explicit state gates.
- `full_fg` is not a halt. `HALTED`, `DELISTED`, and `MISSING` cannot be newly selected or executed.
- Existing halted holdings use last-valid raw close; orders remain backlog and no price is fabricated.
- ETF amount is `sum(previous_actual_weight * authoritative_traded_value)` with existing sequential summation parity.
- `MISSING` remains a quality failure; only `ZERO_AUTHORIZED` contributes exact zero without the generic missing flag.
- Development uses 2024-01-01 through 2026-07-07. Full history runs once after bounded gates pass.
- AFML full-history uses `full_history_acceptance=True` once and a new output directory.

## File Structure

```text
etf_tricks/data_gateway.py       validate and scan daily_market_state
etf_tricks/execution.py          state-aware tensor, execution gate, backlog
etf_tricks/lab.py                state loading, formation gate, orchestration
etf_tricks/result.py             authoritative amount and audit counts
etf_tricks/validation.py         state/amount/lineage gates
tests/etf_tricks/                focused and bounded integration tests
tests/etf_tricks/afml/           source-quality and full-scope gates
docs/validation/                 fresh ETF acceptance evidence
docs/etf_tricks/afml/            fresh AFML acceptance evidence
```

---

### Task 1: Add the manifest-backed market-state adapter

**Files:**
- Modify: `etf_tricks/data_gateway.py`
- Modify: `tests/etf_tricks/test_data_gateway.py`

**Interfaces:**
- Consumes: ready `daily_market_state.json` and requested bounds.
- Produces: `DataGateway.scan_market_state(start, end, tickers=None) -> pd.DataFrame`.

- [ ] **Step 1: Write failing contract tests**

```python
def test_scan_market_state_returns_governed_columns(gateway):
    result = gateway.scan_market_state("2024-01-02", "2024-01-03", ["1101"])
    assert result.columns.tolist() == [
        "date", "ticker", "market_state", "amount_state",
        "authoritative_traded_value", "amount_zero_authorized",
        "exchange_tradable", "full_delivery", "source_available_date",
        "earliest_execution_session", "state_reason",
    ]
    assert not result.duplicated(["date", "ticker"]).any()
```

Also reject a non-ready manifest, missing requested coverage, invalid enums, `ZERO_AUTHORIZED` with nonzero amount, and `MISSING` with numeric amount.

- [ ] **Step 2: Run and confirm failure**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_data_gateway.py -q
```

Expected: FAIL because `scan_market_state` is absent.

- [ ] **Step 3: Implement the adapter**

Add `earliest_execution_session` to `_DATE_COLUMNS` and `daily_market_state: ("date", "ticker")` to `_LOGICAL_KEYS`. Call `scan_artifact` with predicate pushdown; never fallback to DPV or unmanifested files. Validate states against `{TRADING,HALTED,DELISTED,MISSING}` and amounts against `{OBSERVED,ZERO_AUTHORIZED,MISSING}`.

- [ ] **Step 4: Run and commit**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_data_gateway.py -q
git add etf_tricks/data_gateway.py tests/etf_tricks/test_data_gateway.py
git commit -m "feat: read governed daily market state"
```

### Task 2: Gate formation and execution without changing valuation

**Files:**
- Modify: `etf_tricks/execution.py`
- Modify: `etf_tricks/lab.py`
- Modify: `tests/etf_tricks/test_execution.py`
- Modify: `tests/etf_tricks/test_integration.py`

**Interfaces:**
- Consumes: Task 1 state frame.
- Produces: `PreparedExecutionMarket.exchange_tradable`, `market_state_code`, and state-gated monthly targets.

- [ ] **Step 1: Write the halt/resumption test**

Use three sessions: 1101 is held, session two is `HALTED` with no current price, session three resumes. Assert unchanged shares and last-valid valuation on day two, zero executed shares with nonzero backlog, and resumed execution on day three.

```python
assert day2_holding.shares == day1_holding.shares
assert day2_holding.raw_close == day1_holding.raw_close
assert day2_holding.stale_price_days == 1
assert day2_trades.executed_shares.abs().sum() == 0
assert day2_trades.unfilled_shares.abs().sum() > 0
assert day3_trades.executed_shares.abs().sum() > 0
```

- [ ] **Step 2: Test full-delivery and missing-state behavior**

Assert `TRADING + full_delivery=True` executes by default. Assert `MISSING` cannot execute, emits `market_state_missing`, and prevents READY validation.

- [ ] **Step 3: Run and confirm failure**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_execution.py tests/etf_tricks/test_integration.py -q
```

- [ ] **Step 4: Extend tensors and execution gate**

Index state matrices with existing date/ticker codes. `_current_trade_price` returns `None` unless same-day state is `TRADING` and `exchange_tradable=True`. Valuation still uses current or last-valid raw close and never future state.

- [ ] **Step 5: Gate formation with exact-date after-close state**

Load state through `end`, join exact formation-date state to features, and keep only `TRADING`. Record excluded state/reason counts. Formation-close state only determines next-month targets.

- [ ] **Step 6: Freeze lineage, run parity, and commit**

Add `daily_market_state` to `_manifest_hashes()` and pass state once into the prepared market.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_execution.py tests/etf_tricks/test_integration.py tests/etf_tricks/test_features.py tests/etf_tricks/test_universe.py -q
git add etf_tricks/execution.py etf_tricks/lab.py tests/etf_tricks/test_execution.py tests/etf_tricks/test_integration.py
git commit -m "feat: enforce market state in ETF execution"
```

### Task 3: Make ETF amount consume authoritative state amounts

**Files:**
- Modify: `etf_tricks/result.py`
- Modify: `etf_tricks/lab.py`
- Modify: `etf_tricks/validation.py`
- Modify: `tests/etf_tricks/test_result.py`
- Modify: `tests/etf_tricks/test_validation.py`

**Interfaces:**
- Consumes: prior holdings plus `authoritative_traded_value`, `amount_state`, and `amount_zero_authorized`.
- Produces: `etf_amount`, `missing_traded_value_count`, `status_zero_authorized_count`, `market_state_missing_count`, and `amount_quality_state`.

- [ ] **Step 1: Write the three-way amount test**

```python
def _state(date, ticker, amount_state, value, authorized):
    market_state = {
        "OBSERVED": "TRADING",
        "ZERO_AUTHORIZED": "HALTED",
        "MISSING": "MISSING",
    }[amount_state]
    return {
        "date": date,
        "ticker": ticker,
        "market_state": market_state,
        "amount_state": amount_state,
        "authoritative_traded_value": value,
        "amount_zero_authorized": authorized,
    }


market_state = pd.DataFrame([
    _state(day2, "1101", "OBSERVED", 1_000.0, False),
    _state(day2, "1102", "ZERO_AUTHORIZED", 0.0, True),
    _state(day2, "1103", "MISSING", np.nan, False),
])
row = attach_etf_amount(daily, holdings, market_state).query("date == @day2").iloc[0]
assert row.etf_amount == pytest.approx(0.5 * 1_000.0)
assert row.status_zero_authorized_count == 1
assert row.missing_traded_value_count == 1
assert row.market_state_missing_count == 1
assert row.amount_quality_state == "MISSING"
```

- [ ] **Step 2: Test zero-only quality and summation parity**

With only observed and authorized-zero rows, assert zero generic missing count and no new quality flag. Compare observed-only results byte-for-byte or with the repository's existing exact float expectations.

- [ ] **Step 3: Run and confirm failure**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_result.py tests/etf_tricks/test_validation.py -q
```

- [ ] **Step 4: Replace the amount join source**

Keep previous-session holdings alignment. Reject cross-field inconsistencies before aggregation and retain `_sequential_float_sum`.

- [ ] **Step 5: Extend result validation**

Reject non-finite/negative observed amounts, unresolved `MISSING` in READY, suppressed counts, and any authorized zero that increments the generic missing count.

- [ ] **Step 6: Run and commit**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_result.py tests/etf_tricks/test_validation.py tests/etf_tricks/test_integration.py -q
git add etf_tricks/result.py etf_tricks/lab.py etf_tricks/validation.py tests/etf_tricks/test_result.py tests/etf_tricks/test_validation.py tests/etf_tricks/test_integration.py
git commit -m "fix: authorize ETF amount from market state"
```

### Task 4: Pass bounded 13-ETF readiness

**Files:**
- Modify only for defects: files from Tasks 1-3.
- Create outside Git: `.artifacts/etf_tricks/market-state-bounded-20240101-20260707-v1/`

**Interfaces:**
- Consumes: ready canonical artifacts and updated public API.
- Produces: bounded 13-ETF result with no unresolved amount rows.

- [ ] **Step 1: Run all ETF tests**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks -q
```

- [ ] **Step 2: Run one ETF or one bounded engine slice**

Use 2024-01-01 through 2026-07-07. Confirm Daily coverage, accounting identity, state lineage, halt backlog, and zero amount-quality failure.

- [ ] **Step 3: Run all 13 ETFs over the same interval**

```python
lab = ETFTrickLab.from_data_analysts(
    r"C:\Users\ChastLai\Documents\量化交易積木\DataAnalysts"
)
result = lab.run_all(
    start_date="2024-01-01",
    end_date="2026-07-07",
    initial_capital=10_000_000,
)
report = lab.validate(result)
assert report.status == "READY"
assert set(result.daily_etf.etf_id) == set(ETF_IDS)
assert result.daily_etf.missing_traded_value_count.sum() == 0
```

- [ ] **Step 4: Verify round-trip**

Write to a new directory, read through `ETFTrickResult.read`, compare all six tables, hashes, state identity, and readiness. Do not overwrite v5 or commit `.artifacts`.

### Task 5: Run the single full-history ETF and AFML acceptance

**Files:**
- Modify: `docs/validation/etf-tricks-readiness.md`
- Modify: `docs/etf_tricks/afml/2026-08-27-afml-readiness.md`
- Create outside Git: `.artifacts/etf_tricks/full-history-20050103-20260707-v6/`
- Create outside Git: `.artifacts/etf_afml/full-history-post-market-state-20260828-v1/`

**Interfaces:**
- Consumes: all bounded gates and frozen canonical manifests.
- Produces: one v6 ETF result and one AFML full-history attempt.

- [ ] **Step 1: Freeze pre-run identities**

Record SHA-256 for calendar, DPV, chip, sales, financials, security master, and market state plus code/spec hashes. Abort if any scoped verify/store audit is not ready.

- [ ] **Step 2: Execute one 13-ETF full-history rebuild**

Use 2005-01-03 through 2026-07-07 and v6. Require all 13 IDs, unique Daily keys, no fully uninvested gap, zero missing amount, exact prior-weight reconciliation, and full round-trip.

- [ ] **Step 3: Reconcile the historical blockers**

Require all 441 constituent-day joins to be observed or exact zero-authorized; the 334 unique keys must match digest `0256a5b089dcf8410d9a6ec943d50e41d688cd3fa8a9cab318c59ff7e9858bfb`. Prove 9157's 2010-10-06 resumption did not rewrite prior states.

- [ ] **Step 4: Run the public AFML facade once**

```python
from etf_tricks.afml import AFMLConfig, ETFAFMLLab

afml = ETFAFMLLab.from_data_analysts(
    r"C:\Users\ChastLai\Documents\量化交易積木\DataAnalysts"
)
dataset = afml.build_all(
    result,
    config=AFMLConfig(),
    mode="train",
    train_start="2005-01-03",
    train_end="2020-12-31",
    validation_end="2023-12-31",
    test_end="2026-07-07",
    full_history_acceptance=True,
)
manifest = dataset.write(
    r"C:\Users\ChastLai\Documents\量化交易Workflow\.worktrees\etf-afml-dataset\.artifacts\etf_afml\full-history-post-market-state-20260828-v1"
)
```

The directory must not already exist. Do not add an ad hoc runner or bypass
`full_history_acceptance`; if the directory exists, stop and choose the next
explicit version in the plan before running.

- [ ] **Step 5: Validate AFML artifacts**

Require 13 IDs, complete bar membership, `bar_amount == sum(member etf_amount)`, valid FFD/stationarity evidence, PIT-safe feature/label schemas, future-append invariance, hashes, and round-trip. Any failure blocks READY.

- [ ] **Step 6: Update evidence docs and commit**

Separate verified facts from PIT revision and VPIN/Kyle/ATR/ADX/VIX source limitations.

```powershell
git add docs/validation/etf-tricks-readiness.md docs/etf_tricks/afml/2026-08-27-afml-readiness.md
git commit -m "docs: record post-repair AFML acceptance"
```
