# TEJ-only ETF Tricks Market State Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` task-by-task. This plan is approved;
> do not reopen settled design choices.

**Goal:** Consume the certified TEJ-only DataAnalysts `daily_market_state`, enforce
halt and lifecycle rules in ETF execution, compute `etf_amount` without turning
delisted holdings into missing/zero observations, rebuild all 13 ETF Tricks, and
resume bounded-then-full AFML acceptance.

**Architecture:** DataAnalysts remains the only owner of market-state inference.
Workflow reads one manifest-backed state table and never reconstructs state from
APIPRCD/APISTKATTR, exchange websites, or an implicit fallback. Valuation uses
current or last-valid raw close; execution requires same-session
`TRADING + exchange_tradable`; amount uses prior-session actual economic weights
and the state's authoritative amount. Lifecycle liquidation is a separate accounting
event driven by `security_master.delist_date`.

**Upstream authority:**
`C:\Users\ChastLai\Documents\量化交易積木\.worktrees\daily-market-state\docs\superpowers\specs\2026-08-31-tej-only-daily-market-state-design.md`

## Frozen constraints

- Valid published `market_state` values are `TRADING`, `HALTED`, `MISSING`.
  `DELISTED` is not a published row; `date >= delist_date` is outside the active
  lifecycle.
- Valid `amount_state` values are `OBSERVED`, `ZERO_AUTHORIZED`, `MISSING`.
  `HALTED + OBSERVED` is valid when `susp_fg=Y` and APIPRCD reports a valid
  positive amount. It remains non-executable and the observed amount is retained.
- `full_delivery` is not a halt.
- `MISSING` never silently falls back to DPV or zero. Only
  `ZERO_AUTHORIZED + amount_zero_authorized=True + amount=0` contributes exact
  zero without a generic missing-quality count.
- A delisting date on a non-session maps to the first governed session on or after
  that date. The position is force-liquidated once at the last valid raw close,
  charged normal sell costs, permanently blocked from target/backlog repurchase,
  and described as accounting settlement rather than an exchange fill claim.
- On the liquidation session, the prior holding is removed from `etf_amount`
  exposure before joining state. It is neither missing nor authorized zero, and
  remaining weights are not renormalized.
- Development order is fixture -> 1–2 ETF 2024-01-01..2026-07-07 -> 13 ETF same
  interval. Extend to 2020 only for documented observation insufficiency. Full
  history is forbidden until every bounded gate passes.
- Use this repo's `.venv\Scripts\python.exe`; install no packages; never overwrite
  existing result directories or commit runtime artifacts.

---

### Task 1: Add the TEJ-only manifest adapter

**Files:**

- Modify: `etf_tricks/data_gateway.py`
- Modify: `tests/etf_tricks/test_data_gateway.py`

**RED tests:**

1. `scan_market_state(start, end, tickers)` returns a unique `(date,ticker)` table
   with the exact governed state/amount/availability/lifecycle/lineage fields.
2. Reject a non-ready manifest, missing requested coverage, invalid enum, any
   `DELISTED` row, inconsistent observed/zero/missing amount combinations, and
   missing required lifecycle/manifest hashes.
3. Prove scan uses predicate pushdown and never falls back to DPV or unmanifested
   paths.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_data_gateway.py -q
```

Expected RED: `scan_market_state` is absent.

**Minimal GREEN:** register the artifact key/date columns, scan through the existing
manifest path, validate explicit conditions (not `assert`), and return stable dtypes
and column order. Do not add Mongo or web access to Workflow.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_data_gateway.py -q
git add etf_tricks/data_gateway.py tests/etf_tricks/test_data_gateway.py
git commit -m "feat: read TEJ-only daily market state"
```

### Task 2: Enforce halt state without changing valuation semantics

**Files:**

- Modify: `etf_tricks/execution.py`
- Modify: `tests/etf_tricks/test_execution.py`

**RED tests:**

1. Held security: `TRADING -> HALTED/ZERO_AUTHORIZED -> TRADING` retains the
   last valid raw close on the halted session, executes zero shares with backlog,
   and resumes backlog only on the later trading session.
2. `HALTED + OBSERVED + positive amount` remains non-executable but preserves
   observed amount in the prepared market channel.
3. `TRADING + full_delivery=True` executes normally.
4. `MISSING` is non-executable and emits a specific diagnostic; it is not treated
   as a halt or zero-authorized state.
5. Input row order and prepared/non-prepared paths produce identical ledgers.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_execution.py -q
```

Expected RED: prepared execution market has no independent state channel.

**Minimal GREEN:** extend the existing date×ticker arrays with state and
tradability codes. `_current_trade_price` returns raw close only for same-session
`TRADING + exchange_tradable`; mark-to-market continues to use current or last
valid raw close. Preserve the vectorized lookup path and existing accounting.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_execution.py -q
git add etf_tricks/execution.py tests/etf_tricks/test_execution.py
git commit -m "feat: gate ETF execution with market state"
```

### Task 3: Make delisting session-safe and permanently non-rebuyable

**Files:**

- Modify: `etf_tricks/execution.py`
- Modify: `tests/etf_tricks/test_execution.py`

**RED tests:**

1. A delist date equal to a governed session liquidates exactly once.
2. A weekend/non-session delist date liquidates on the first governed session
   `>= delist_date`.
3. A current-month target and backlog containing the ticker cannot repurchase it
   on or after the effective delist session.
4. Liquidation uses the last valid raw close and existing commission/tax rules;
   missing last-valid price fails closed.
5. No ordinary buy/sell record is emitted after the forced settlement for that
   ticker.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_execution.py -q
```

Expected RED: current code requires `delist_date == date` and rebuilds targets
after removing the ticker from schedule/backlog.

**Minimal GREEN:** precompute each ticker's effective delist session with the
governed calendar; maintain a permanent lifecycle-block set; filter monthly
targets, desired orders, and backlog before allocation; preserve the existing
forced-settlement record and cost semantics.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_execution.py -q
git add etf_tricks/execution.py tests/etf_tricks/test_execution.py
git commit -m "fix: enforce effective delist liquidation"
```

### Task 4: Compute authoritative ETF amount with lifecycle exclusion

**Files:**

- Modify: `etf_tricks/result.py`
- Modify: `etf_tricks/lab.py`
- Modify: `etf_tricks/validation.py`
- Modify: `tests/etf_tricks/test_result.py`
- Modify: `tests/etf_tricks/test_validation.py`

**RED tests:**

1. Previous-session weights × `OBSERVED` amount reconcile exactly using the
   current sequential summation order.
2. `HALTED + ZERO_AUTHORIZED` contributes zero, increments
   `status_zero_authorized_count`, and does not increment generic missing count.
3. `HALTED + OBSERVED` contributes its positive weighted amount even though it is
   non-executable.
4. `MISSING` contributes zero only for arithmetic continuity, increments both
   missing counters and blocks READY.
5. A holding whose `delist_date <= current_date` is removed before the state join:
   it contributes nothing and increments neither missing nor zero-authorized count;
   remaining weights are not renormalized.
6. Vectorized relational alignment remains free of row-wise daily iteration.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_result.py tests/etf_tricks/test_validation.py -q
```

Expected RED: amount consumes raw DPV and lacks lifecycle/amount-state semantics.

**Minimal GREEN:** accept the governed market-state frame and security master;
align each daily result with the prior session's holdings; exclude delisted
exposure by current result date; validate cross-field invariants before aggregation;
preserve `_sequential_float_sum`; add explicit audit counts and
`amount_quality_state`.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_result.py tests/etf_tricks/test_validation.py tests/etf_tricks/test_integration.py -q
git add etf_tricks/result.py etf_tricks/lab.py etf_tricks/validation.py tests/etf_tricks/test_result.py tests/etf_tricks/test_validation.py tests/etf_tricks/test_integration.py
git commit -m "fix: reconcile ETF amount with lifecycle state"
```

### Task 5: Wire formation, lineage, and public orchestration

**Files:**

- Modify: `etf_tricks/lab.py`
- Modify: `etf_tricks/universe.py` only if the prepared interface requires it
- Modify: `tests/etf_tricks/test_integration.py`
- Modify: `tests/etf_tricks/test_universe.py` only if changed

**RED tests:**

1. Exact formation-date state admits only `TRADING`; `HALTED` and `MISSING` have
   distinct audit reasons. `full_delivery=True` does not exclude.
2. `lab.run_all` scans state once, passes the same prepared identity to all 13
   engines, and records the `daily_market_state` manifest hash.
3. Missing or stale upstream state coverage fails before ETF calculation; no DPV
   fallback occurs.
4. A bounded round trip preserves all six result tables, state identity, lifecycle
   diagnostics, and hashes.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks/test_integration.py tests/etf_tricks/test_universe.py -q
```

**Minimal GREEN:** load state through the requested end date, join exact-date
formation state, pass the state arrays once to execution, call the new amount API,
and add the manifest hash/config identity to metadata.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/etf_tricks -q
git add etf_tricks/lab.py etf_tricks/universe.py tests/etf_tricks/test_integration.py tests/etf_tricks/test_universe.py
git commit -m "feat: orchestrate TEJ-only ETF market state"
```

### Task 6: Bounded readiness and staged full-history acceptance

**Runtime outputs:** git-ignored, new versioned directories only.

1. Run the entire ETF unit/integration suite and `python -O` contract checks.
2. Run a hand-checkable/one-ETF slice, then all 13 ETFs for
   `2024-01-01..2026-07-07`. Record wall time, peak memory, input hashes, all 13
   IDs, Daily key uniqueness, accounting identity, no fully uninvested gaps, amount
   reconciliation, halt/resume cases, delist exclusions, and round trip.
3. Extend to `2020-01-01..2026-07-07` only when a named AFML observation gate is
   otherwise impossible, and record the reason.
4. Only after bounded READY and a certified upstream full-history state candidate,
   run one `full_history_acceptance=True` 13-ETF rebuild into a new directory.
5. Resume the AFML authoritative Prompt in this order: source-capability matrix ->
   Dollar bars -> FFD/ADF -> SADF/QADF/CADF -> features -> labels. Validate
   `bar_amount == sum(member etf_amount)`, availability-time replay, train-only
   calibration, 13 IDs, hashes, and Notebook imports.
6. Update `docs/validation/etf-tricks-readiness.md` and
   `docs/etf_tricks/afml/2026-08-27-afml-readiness.md` with separate sections
   `目前可用` and `目前缺失／限制`. A failed gate remains NOT READY and retains
   its evidence.

No canonical DataAnalysts publication, branch merge, push, or replacement of an
existing artifact directory is authorized by this plan itself.
