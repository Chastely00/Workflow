# ETF `run_all()` Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the 2024-01-01 through 2026-07-07 `ETFTrickLab.run_all()` wall time without changing PIT selection, accounting, ledgers, Daily NAV, ETF amount, audit tables, or public APIs.

**Architecture:** Replace per-ticker pandas window construction with one bounded `date × ticker` numeric panel evaluated across all formation dates. Preserve the path-dependent Decimal execution loop, but share one prepared dense market lookup across all 13 ETFs and stop looking up zero-share historical positions. Freeze a fresh pre-change 2024 oracle before production edits and compare every canonical table after each slice.

**Tech Stack:** Python 3.12, pandas 3.0, NumPy 2.4, pyarrow 25, pytest, Decimal accounting.

**Spec:** `docs/etf_tricks/prompts/02-performance-optimization-prompt.md` and `docs/etf_tricks/performance/2026-08-27-run-all-performance-study.md`

## Global Constraints

- Development profiling and repeated tests use `2024-01-01` through `2026-07-07`; do not run 2005 history during iteration.
- Preserve the approved PIT, liquidity, stable-ranking, integer-share, fee, cash, corporate-action, stale-price, no-empty-holdings, NAV and ETF-amount semantics.
- Preserve all six canonical result tables, public property behavior, schemas, unique keys, flags and manifest/spec identities.
- Feature floating reductions may differ only at unavoidable ULP scale and only when masks, ranks, targets, weights and every downstream ledger value remain governed-equivalent.
- Shares, trades, fees, tax, cash and reconciliation remain exact; Decimal must stay in the accounting path.
- No new dependencies, multiprocessing, GPU, JIT, validation bypass, audit reduction or hidden cache.
- Preserve unrelated working-tree changes and keep benchmark outputs under ignored `.artifacts/etf_tricks/performance/`.

---

### Task 1: Freeze the fresh 2024–2026 oracle

**Files:**
- Create ignored artifact: `.artifacts/etf_tricks/performance/baseline-20240101-20260707/`
- Create ignored evidence: `.artifacts/etf_tricks/performance/baseline-20240101-20260707/performance.json`

**Interfaces:**
- Consumes: current `ETFTrickLab.run_all(start_date, end_date, initial_capital)`.
- Produces: six canonical parquet tables, `result_manifest.json`, wall time, row counts, environment and source/spec hashes.

- [ ] **Step 1: Execute one pre-change representative run**

Run from the isolated worktree with the repository `.venv` and the main checkout's absolute DataAnalysts root:

```powershell
@'
from decimal import Decimal
from pathlib import Path
from time import perf_counter
import json, platform, sys
import numpy as np
import pandas as pd
import pyarrow
from etf_tricks import ETFTrickLab

repo = Path(r"C:\Users\ChastLai\Documents\量化交易Workflow")
output = repo / ".artifacts/etf_tricks/performance/baseline-20240101-20260707"
lab = ETFTrickLab.from_data_analysts(repo / "DataAnalysts")
started = perf_counter()
result = lab.run_all(
    start_date="2024-01-01",
    end_date="2026-07-07",
    initial_capital=Decimal("10000000"),
)
elapsed = perf_counter() - started
manifest = result.write(output)
evidence = {
    "wall_seconds": elapsed,
    "python": sys.version,
    "platform": platform.platform(),
    "pandas": pd.__version__,
    "numpy": np.__version__,
    "pyarrow": pyarrow.__version__,
    "rows": {name: entry["rows"] for name, entry in manifest["tables"].items()},
    "metadata": result.metadata,
}
(output / "performance.json").write_text(
    json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2),
    encoding="utf-8",
)
print(json.dumps({"wall_seconds": elapsed, "rows": evidence["rows"]}, sort_keys=True))
'@ | C:\Users\ChastLai\Documents\量化交易Workflow\.venv\Scripts\python.exe -
```

Expected: one completed run; `daily_etf=7878`, `monthly_targets=3967`, and all six table hashes recorded.

- [ ] **Step 2: Reload the oracle and verify table hashes**

```powershell
C:\Users\ChastLai\Documents\量化交易Workflow\.venv\Scripts\python.exe -c "from etf_tricks.result import ETFTrickResult; r=ETFTrickResult.read(r'C:\Users\ChastLai\Documents\量化交易Workflow\.artifacts\etf_tricks\performance\baseline-20240101-20260707'); print(len(r.daily_etf), len(r.monthly_targets))"
```

Expected: `7878 3967` with no hash or row-count error.

---

### Task 2: Add multi-formation 2D feature computation

**Files:**
- Modify: `etf_tricks/features.py`
- Modify: `etf_tricks/lab.py`
- Modify: `tests/etf_tricks/test_features.py`
- Modify: `tests/etf_tricks/test_integration.py`

**Interfaces:**
- Consumes: normalized daily/chip/sales/financial panels and `TradingCalendar`.
- Produces: `PITFeatureEngine.compute_many(formation_dates: object) -> dict[pd.Timestamp, pd.DataFrame]`; existing `compute()` delegates to the same governed calculation.

- [ ] **Step 1: Write the failing multi-formation behavior test**

Add a test using the existing hand-checkable feature fixture. Request two formation dates and assert literal formation keys, ticker order, observation counts, momentum dates and the existing expected signal values. Add one non-TWSE-calendar daily/chip row whose date falls inside the date range and assert that neither formation result changes.

```python
def test_compute_many_uses_one_calendar_aligned_panel_and_ignores_noncalendar_rows():
    calendar, panels, formation_two = _panels()
    formation_one = pd.Timestamp(calendar.days[-2])
    noncalendar_date = pd.Timestamp("2025-08-23")
    panels["daily_price_volume"] = pd.concat(
        [
            panels["daily_price_volume"],
            pd.DataFrame(
                [
                    {
                        "date": noncalendar_date,
                        "ticker": "1101",
                        "close": 9999.0,
                        "adj_close": 9999.0,
                        "volume": 9999.0,
                        "traded_value": 9999.0,
                        "turnover": 9999.0,
                        "market_cap": 9999.0,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    panels["daily_chip"] = pd.concat(
        [
            panels["daily_chip"],
            pd.DataFrame(
                [
                    {
                        "date": noncalendar_date,
                        "ticker": "1101",
                        "qfii_examt": 9999.0,
                        "fund_examt": 9999.0,
                        "dlrp_examt": 9999.0,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    result = PITFeatureEngine(calendar, panels).compute_many(
        (formation_one, formation_two)
    )
    assert tuple(result) == (formation_one, formation_two)
    assert result[formation_two]["ticker"].tolist() == ["1101", "1102"]
    assert result[formation_two].iloc[0]["adv20_observation_count"] == 20
    assert result[formation_two].iloc[0]["momentum_recent_date"] == calendar.days[-22]
    assert result[formation_two].iloc[0]["momentum_old_date"] == calendar.days[-253]
```

- [ ] **Step 2: Run the focused test and verify RED**

```powershell
C:\Users\ChastLai\Documents\量化交易Workflow\.venv\Scripts\python.exe -m pytest tests\etf_tricks\test_features.py::test_compute_many_uses_one_calendar_aligned_panel_and_ignores_noncalendar_rows -q
```

Expected: FAIL because `PITFeatureEngine` has no `compute_many` method.

- [ ] **Step 3: Implement the bounded prepared panel**

In `features.py`:

- cache `pd.DatetimeIndex(calendar.days)` once;
- validate and normalize requested formation dates;
- bound the local calendar at 252 days before the first formation and the last formation;
- factorize sorted ticker strings once;
- map only rows with valid non-negative local calendar and ticker codes;
- build float64 matrices for close, adjusted close, volume, traded value, turnover, market cap and three chip fields;
- build one presence matrix so a missing row differs from a row containing NaNs;
- calculate 20/60/80/252-day signals across all tickers with NumPy reductions;
- merge the existing PIT sales/ROE audit values by ticker for each formation;
- return ticker-sorted DataFrames with the exact existing columns;
- make `compute(formation)` call `compute_many((formation,))[formation]`.

Do not materialize a full 23,376-day matrix; use only the bounded local calendar.

- [ ] **Step 4: Run all feature tests and verify GREEN**

```powershell
C:\Users\ChastLai\Documents\量化交易Workflow\.venv\Scripts\python.exe -m pytest tests\etf_tricks\test_features.py -q -W error
```

Expected: all feature tests pass without warnings.

- [ ] **Step 5: Write the failing facade batching test**

In `test_integration.py`, use a real fixture and monkeypatch only `PITFeatureEngine.compute_many` with a counting wrapper around the real method. Assert one call containing all formation dates and retain all existing 13-ETF output assertions. This catches regression back to per-formation matrix preparation; it does not mock returned data.

- [ ] **Step 6: Run the facade batching test and verify RED**

Expected: FAIL because `ETFTrickLab.run_all()` still calls `compute()` inside the formation loop.

- [ ] **Step 7: Make `run_all()` consume `compute_many()` once**

Compute the first needed warm-up date from the full calendar and earliest formation, trim daily/chip DataFrames after contract validation, call `compute_many(formation_dates)` once, and index the returned frames inside the existing formation loop. Do not alter Universe calls or candidate ordering.

- [ ] **Step 8: Compare Slice 1 to the fresh oracle**

Run 2024–2026 once into `.artifacts/etf_tricks/performance/slice1-...`, reload baseline and new results, sort all six tables by governed keys, and compare schema, rows, masks, targets and ledgers. Report maximum floating differences separately. Expected: target ticker/rank/weight and every stateful ledger are exact; feature stage <=5 seconds or the slice remains incomplete.

- [ ] **Step 9: Commit Slice 1**

```powershell
git add etf_tricks/features.py etf_tricks/lab.py tests/etf_tricks/test_features.py tests/etf_tricks/test_integration.py
git commit -m "perf: batch ETF feature computation"
```

---

### Task 3: Share a prepared execution market and stop zero-position lookups

**Files:**
- Modify: `etf_tricks/execution.py`
- Modify: `etf_tricks/lab.py`
- Modify: `tests/etf_tricks/test_execution.py`
- Modify: `tests/etf_tricks/test_integration.py`

**Interfaces:**
- Consumes: execution-market DataFrame with unique `(date, ticker)` rows.
- Produces: immutable `PreparedExecutionMarket`; `PortfolioExecutionEngine.prepare_market(frame)`; `run()` accepts either the prepared object or the existing DataFrame.

- [ ] **Step 1: Write the failing prepared-market equivalence test**

Use the existing three-day hand ledger. Run once with the DataFrame and once with `prepare_market(frame)`, then use `pd.testing.assert_frame_equal` on daily, holdings, trades and diagnostics. Expected production mutation caught: prepared lookup returning a wrong close, missing flag or ticker mapping.

- [ ] **Step 2: Run focused test and verify RED**

Expected: FAIL because `prepare_market` does not exist.

- [ ] **Step 3: Implement immutable dense execution lookup**

Normalize and duplicate-check once, factorize dates and tickers, and build dense float64 arrays for raw close, adjusted close and traded value. Store date/ticker position maps. Preserve `Decimal(str(value))` conversion at the point where accounting consumes prices. `run(DataFrame)` remains backward compatible by preparing internally; `run(PreparedExecutionMarket)` reuses the object.

- [ ] **Step 4: Write the failing exited-ticker lookup regression**

Use a real prepared market subclass/test double that counts real lookup calls while preserving all behavior. Construct two target months where a ticker is fully exited in month one. Assert it receives no price-pair lookups after it is absent from positive holdings, schedule start/target and current targets. This catches reintroduction of `set(shares)` without filtering.

- [ ] **Step 5: Filter active ticker scope and cache calendar/targets**

Build current tickers from `{ticker for ticker, quantity in shares.items() if quantity > 0}`, schedule dictionaries and current month targets. Precompute `(month, k, N)` for each execution date and pre-group target rows by month. Do not prune backlog or schedule state.

- [ ] **Step 6: Make `run_all()` prepare market once**

Create one prepared market before the 13-ETF loop and pass the same immutable object to every `engine.run()` call.

- [ ] **Step 7: Run execution and integration tests**

```powershell
C:\Users\ChastLai\Documents\量化交易Workflow\.venv\Scripts\python.exe -m pytest tests\etf_tricks\test_execution.py tests\etf_tricks\test_integration.py -q -W error
```

Expected: all tests pass with exact hand-ledger values.

- [ ] **Step 8: Compare Slice 2 to the fresh oracle and benchmark**

Run 2024–2026 into `.artifacts/etf_tricks/performance/slice2-...`. Require exact daily shares, trades, cash, fees, tax, corporate actions, holdings and NAV. Execution total must be <=15 seconds; otherwise re-profile before another implementation.

- [ ] **Step 9: Commit Slice 2**

```powershell
git add etf_tricks/execution.py etf_tricks/lab.py tests/etf_tricks/test_execution.py tests/etf_tricks/test_integration.py
git commit -m "perf: share ETF execution market lookup"
```

---

### Task 4: Re-profile and decide the second batch

**Files:**
- Update: `docs/etf_tricks/performance/2026-08-27-run-all-performance-study.md`
- Create ignored evidence: `.artifacts/etf_tricks/performance/slice2-.../performance.json`

**Interfaces:**
- Consumes: Slice 2 public API and frozen baseline.
- Produces: new stage timing, speedup, output equivalence report and next-bottleneck decision.

- [ ] **Step 1: Run the same lightweight stage wrappers**

Use exactly `2024-01-01` through `2026-07-07`, the same capital and source manifests. Record feature, Universe, execution, ETF amount, read and residual times.

- [ ] **Step 2: Run the scoped full suite**

```powershell
C:\Users\ChastLai\Documents\量化交易Workflow\.venv\Scripts\python.exe -m pytest tests\etf_tricks tests\test_verify_environment.py -q -W error
C:\Users\ChastLai\Documents\量化交易Workflow\.venv\Scripts\python.exe -m pip check
```

- [ ] **Step 3: Update the study with measured before/after evidence**

Record exact wall time, stage speedups, row counts, equality status, peak-memory limitation and whether Universe, ETF amount or I/O is now the highest-value next slice. Do not claim the <=30 second final target unless measured.

- [ ] **Step 4: Commit the checkpoint evidence**

```powershell
git add docs/etf_tricks/performance/2026-08-27-run-all-performance-study.md
git commit -m "docs: record ETF run_all optimization checkpoint"
```
