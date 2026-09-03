# Task 2 Review Package

Rebuilt after narrow market-calendar fix.

## FILE: plans\sdd\historical-universe\task-2-brief.md
```
# Task 2 Brief

### Task 2: Historical Security Panel Builder

**Files:**
- Modify: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\security_panel.py`
- Test: `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_security_panel.py`

**Interfaces:**
- Consumes:
  - `daily_prices: list[dict[str, Any]]`
  - `security_master: list[dict[str, Any]]`
  - `trading_calendar: list[dict[str, Any]]`
  - `daily_tradability: list[dict[str, Any]] | None`
- Produces:
  - `build_historical_security_panel(...) -> tuple[list[dict[str, Any]], dict[str, Any]]`
  - Rows keyed by `(as_of_date, ticker)`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_historical_security_panel.py`:

```python
from data_analysts.security_panel import build_historical_security_panel


def test_historical_security_panel_uses_next_trading_day_as_effective_date():
    rows, diag = build_historical_security_panel(
        daily_prices=[
            {"date": "2025-01-02", "ticker": "2330", "close": 100, "adj_close": 100, "volume": 10, "traded_value": 1000, "market_cap": 10000, "data_cutoff_at": "2025-01-02T00:00:00Z"},
            {"date": "2025-01-03", "ticker": "2330", "close": 101, "adj_close": 101, "volume": 11, "traded_value": 1111, "market_cap": 10100, "data_cutoff_at": "2025-01-03T00:00:00Z"},
        ],
        security_master=[
            {"ticker": "2330", "stock_name": "TSMC", "market": "TWSE", "listed": True, "security_type": "common_stock", "data_cutoff_at": "2025-01-01T00:00:00Z"}
        ],
        trading_calendar=[
            {"date": "2025-01-02", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
        start_date="2025-01-02",
        end_date="2025-01-03",
    )

    by_date = {row["as_of_date"]: row for row in rows}
    assert by_date["2025-01-02"]["effective_date"] == "2025-01-03"
    assert by_date["2025-01-03"]["effective_date"] == "2025-01-06"
    assert by_date["2025-01-03"]["source_max_date"] == "2025-01-03"
    assert diag["as_of_date_count"] == 2
    assert diag["duplicate_as_of_ticker_count"] == 0


def test_historical_security_panel_adv20_uses_only_past_and_current_values():
    rows, _ = build_historical_security_panel(
        daily_prices=[
            {"date": "2025-01-02", "ticker": "2330", "close": 100, "adj_close": 100, "volume": 1, "traded_value": 10, "market_cap": 10000},
            {"date": "2025-01-03", "ticker": "2330", "close": 101, "adj_close": 101, "volume": 1, "traded_value": 30, "market_cap": 10100},
        ],
        security_master=[{"ticker": "2330", "market": "TWSE", "listed": True, "security_type": "common_stock"}],
        trading_calendar=[
            {"date": "2025-01-02", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
    )

    by_date = {row["as_of_date"]: row for row in rows}
    assert by_date["2025-01-02"]["adv20"] == 10
    assert by_date["2025-01-03"]["adv20"] == 20
```

- [ ] **Step 2: Run tests to verify RED**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q
```

Expected: FAIL because `build_historical_security_panel` does not exist.

- [ ] **Step 3: Implement helper functions**

Add to `src/data_analysts/security_panel.py`:

```python
def build_historical_security_panel(
    daily_prices: list[dict[str, Any]],
    security_master: list[dict[str, Any]],
    trading_calendar: list[dict[str, Any]],
    daily_tradability: list[dict[str, Any]] | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trading_dates = _trading_dates(trading_calendar, start_date=start_date, end_date=end_date)
    next_trading_date = _next_trading_date_map(trading_calendar)
    master_by_ticker = {row["ticker"]: row for row in security_master if row.get("ticker")}
    tradability_by_key = {
        (str(row.get("date")), str(row.get("ticker"))): row
        for row in daily_tradability or []
        if row.get("date") and row.get("ticker")
    }
    price_by_key = {
        (str(row.get("date")), str(row.get("ticker"))): row
        for row in daily_prices
        if row.get("date") and row.get("ticker")
    }
    tickers = sorted({key[1] for key in price_by_key} | set(master_by_ticker))
    adv_by_key = _rolling_adv20(daily_prices)

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    duplicate_count = 0
    missing_effective_count = 0
    for as_of_date in trading_dates:
        effective_date = next_trading_date.get(as_of_date)
        if effective_date is None:
            missing_effective_count += len(tickers)
        for ticker in tickers:
            price = price_by_key.get((as_of_date, ticker))
            if price is None:
                continue
            key = (as_of_date, ticker)
            if key in seen:
                duplicate_count += 1
                continue
            seen.add(key)
            master = master_by_ticker.get(ticker, {})
            tradability = tradability_by_key.get((as_of_date, ticker), {})
            volume = float(price.get("volume") or 0.0)
            listed = bool(master.get("listed", True))
            row = {
                "as_of_date": as_of_date,
                "effective_date": effective_date,
                "source_max_date": price.get("date"),
                "ticker": ticker,
                "stock_name": master.get("stock_name"),
                "market": master.get("market"),
                "security_type": _security_type(master),
                "listed": listed,
                "tradable": bool(tradability.get("tradable", listed and volume > 0)),
                "close": price.get("close"),
                "adj_close": price.get("adj_close"),
                "traded_value": float(price.get("traded_value") or 0.0),
                "market_cap": price.get("market_cap"),
                "adv20": adv_by_key.get((as_of_date, ticker)),
                "data_cutoff_at": price.get("data_cutoff_at") or master.get("data_cutoff_at"),
            }
            rows.append(row)
    diagnostics = {
        "as_of_date_count": len(trading_dates),
        "panel_row_count": len(rows),
        "duplicate_as_of_ticker_count": duplicate_count,
        "effective_date_null_count": missing_effective_count,
        "date_min": min(trading_dates) if trading_dates else None,
        "date_max": max(trading_dates) if trading_dates else None,
    }
    return rows, diagnostics
```

Also add `_trading_dates()`, `_next_trading_date_map()`, and `_rolling_adv20()` with deterministic sorted-date behavior.

- [ ] **Step 4: Run tests**

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src\data_analysts\security_panel.py tests\test_historical_security_panel.py
git commit -m "feat: build historical security panel"
```


```


## FILE: plans\sdd\historical-universe\task-2-report.md
```
STATUS: DONE

Files changed
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\security_panel.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_security_panel.py`
- `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\plans\sdd\historical-universe\task-2-report.md`

RED test command/result
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q`
- Result: FAIL during collection with `ImportError: cannot import name 'build_historical_security_panel' from 'data_analysts.security_panel'`.

GREEN test command/result
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q`
- Result: PASS, `3 passed in 0.03s`.

Self-review notes
- Kept existing `build_security_panel()` behavior unchanged and added an explicit regression test for its latest-only output shape.
- Added `build_historical_security_panel()` as a separate helper so Task 2 does not alter pipeline publish, universe builder, or verify behavior.
- `effective_date` is derived from the next trading day in sorted trading-calendar order.
- `adv20` is computed per ticker from current-and-past `traded_value` only, using a rolling window capped at 20 observations.
- Historical rows are emitted only when a price exists for `(as_of_date, ticker)`, preserving `source_max_date <= as_of_date` for this helper surface.

Concerns
- None for Task 2 scope.

---

Fix update: reviewer findings follow-up

STATUS: DONE

Scope
- Modified only `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\security_panel.py`
- Modified only `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_security_panel.py`
- Preserved existing `build_security_panel()` behavior
- Did not touch pipeline publish, universe builder, or verify gates

Root cause
- `effective_date` used one global next-trading-day map, so `TPEX` rows could inherit `TWSE` next dates.
- Duplicate `(date, ticker)` price rows were collapsed by `price_by_key` dict construction before diagnostics could see them.
- `effective_date_null_count` was incremented per `(as_of_date, ticker universe)` before checking whether a price-backed output row existed.

RED
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q`
- Result: `3 failed, 3 passed`
- Failing regressions:
  - market-specific next trading day expected `8088/TPEX -> 2025-01-07`, got `2025-01-06`
  - duplicate price rows expected first row retained and `duplicate_as_of_ticker_count == 1`, but later row overwrote earlier row
  - null effective-date diagnostics expected `1`, got `2`

Fix
- Added market-aware effective-date resolution:
  - use `trading_calendar.market`-specific next trading day when the security master has a market and that market has calendar coverage for the row date
  - fallback to global next-trading-day map only when the security master lacks a usable market/date-specific market calendar entry
- Replaced price dict overwrite path with `price_rows_by_key` lists so duplicates remain observable and diagnostics count every extra `(as_of_date, ticker)` row
- Emit panel rows from the first price row per `(as_of_date, ticker)` and count `effective_date_null_count` only for rows actually emitted

GREEN
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q`
- Result: `6 passed in 0.02s`

Concerns
- None for requested Task 2 fix scope.

---

Fix update: narrow calendar fallback correction

STATUS: DONE

Scope
- Modified only `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\src\data_analysts\security_panel.py`
- Modified only `C:\Users\ChastLai\Documents\ALF\量化積木\DataAnalysts\tests\test_historical_security_panel.py`
- Preserved existing latest-only `build_security_panel()` behavior

Root cause
- `_effective_date_for_market()` fell back to the global next-trading-day map whenever a known market calendar did not contain the row `as_of_date`.
- That behavior violated the historical universe fail-closed contract: known `market` must use its own market calendar, and missing market coverage must surface as `effective_date = None`.

RED
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q`
- Result: FAIL, `test_historical_security_panel_known_market_does_not_fallback_to_global_calendar`
- Observed bug: TWSE row on `2025-01-03` incorrectly received global/TPEX next date `2025-01-06` instead of `None`

Fix
- Tightened `_effective_date_for_market()` so:
  - known non-blank `market` returns that market's next trading day only when the market calendar contains `as_of_date`
  - known `market` with missing calendar coverage or missing next trading day returns `None`
  - only unknown/blank `market` falls back to the global calendar
- Added regression coverage proving a TWSE security does not inherit a TPEX/global next date when TWSE lacks the `as_of_date`

GREEN
- Command: `$env:PYTHONPATH='src'; python -m pytest tests\test_historical_security_panel.py -q`
- Result: PASS, `7 passed in 0.02s`

Concerns
- None for requested narrow fix scope.

```


## FILE: src\data_analysts\security_panel.py
```
from __future__ import annotations

from typing import Any


def build_security_panel(
    daily_prices: list[dict[str, Any]],
    security_master: list[dict[str, Any]],
    *,
    as_of_date: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    if not daily_prices:
        return as_of_date or "", []
    effective_date = as_of_date or max(str(row["date"]) for row in daily_prices if row.get("date"))
    master_by_ticker = {row["ticker"]: row for row in security_master if row.get("ticker")}
    panel: list[dict[str, Any]] = []
    for price in daily_prices:
        if str(price.get("date")) != effective_date:
            continue
        ticker = price.get("ticker")
        master = master_by_ticker.get(ticker)
        listed = bool(master and master.get("listed", True))
        security_type = _security_type(master)
        volume = float(price.get("volume") or 0.0)
        traded_value = float(price.get("traded_value") or 0.0)
        panel.append(
            {
                "as_of_date": effective_date,
                "source_max_date": price.get("date"),
                "ticker": ticker,
                "stock_name": master.get("stock_name") if master else None,
                "market": master.get("market") if master else None,
                "security_type": security_type,
                "listed": listed,
                "tradable": listed and volume > 0,
                "close": price.get("close"),
                "adj_close": price.get("adj_close"),
                "traded_value": traded_value,
                "market_cap": price.get("market_cap"),
                "adv20": traded_value,
                "data_cutoff_at": price.get("data_cutoff_at") or (master.get("data_cutoff_at") if master else None),
            }
    )
    return effective_date, panel


def build_historical_security_panel(
    daily_prices: list[dict[str, Any]],
    security_master: list[dict[str, Any]],
    trading_calendar: list[dict[str, Any]],
    daily_tradability: list[dict[str, Any]] | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trading_dates = _trading_dates(trading_calendar, start_date=start_date, end_date=end_date)
    global_next_trading_date = _next_trading_date_map(trading_calendar)
    market_next_trading_date = _next_trading_date_by_market(trading_calendar)
    market_trading_dates = _trading_dates_by_market(trading_calendar)
    master_by_ticker = {row["ticker"]: row for row in security_master if row.get("ticker")}
    tradability_by_key = {
        (str(row.get("date")), str(row.get("ticker"))): row
        for row in daily_tradability or []
        if row.get("date") and row.get("ticker")
    }
    price_rows_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    duplicate_count = 0
    for row in daily_prices:
        if not row.get("date") or not row.get("ticker"):
            continue
        key = (str(row.get("date")), str(row.get("ticker")))
        price_rows = price_rows_by_key.setdefault(key, [])
        if price_rows:
            duplicate_count += 1
        price_rows.append(row)
    tickers = sorted({key[1] for key in price_rows_by_key} | set(master_by_ticker))
    adv_by_key = _rolling_adv20(daily_prices)

    rows: list[dict[str, Any]] = []
    missing_effective_count = 0
    for as_of_date in trading_dates:
        for ticker in tickers:
            price_rows = price_rows_by_key.get((as_of_date, ticker))
            if not price_rows:
                continue
            master = master_by_ticker.get(ticker, {})
            effective_date = _effective_date_for_market(
                as_of_date,
                master.get("market"),
                market_next_trading_date=market_next_trading_date,
                market_trading_dates=market_trading_dates,
                global_next_trading_date=global_next_trading_date,
            )
            if effective_date is None:
                missing_effective_count += 1
            tradability = tradability_by_key.get((as_of_date, ticker), {})
            price = price_rows[0]
            volume = float(price.get("volume") or 0.0)
            listed = bool(master.get("listed", True))
            row = {
                "as_of_date": as_of_date,
                "effective_date": effective_date,
                "source_max_date": price.get("date"),
                "ticker": ticker,
                "stock_name": master.get("stock_name"),
                "market": master.get("market"),
                "security_type": _security_type(master),
                "listed": listed,
                "tradable": bool(tradability.get("tradable", listed and volume > 0)),
                "close": price.get("close"),
                "adj_close": price.get("adj_close"),
                "traded_value": float(price.get("traded_value") or 0.0),
                "market_cap": price.get("market_cap"),
                "adv20": adv_by_key.get((as_of_date, ticker)),
                "data_cutoff_at": price.get("data_cutoff_at") or master.get("data_cutoff_at"),
            }
            rows.append(row)
    diagnostics = {
        "as_of_date_count": len(trading_dates),
        "panel_row_count": len(rows),
        "duplicate_as_of_ticker_count": duplicate_count,
        "effective_date_null_count": missing_effective_count,
        "date_min": min(trading_dates) if trading_dates else None,
        "date_max": max(trading_dates) if trading_dates else None,
    }
    return rows, diagnostics


def _security_type(master: dict[str, Any] | None) -> Any:
    if not master:
        return None
    explicit = master.get("security_type")
    if explicit:
        return explicit
    if master.get("main_industry") or master.get("sub_industry"):
        return "common_stock"
    return None


def _trading_dates(
    trading_calendar: list[dict[str, Any]],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[str]:
    dates = sorted(
        {
            str(row.get("date"))
            for row in trading_calendar
            if row.get("date")
            and bool(row.get("is_trading_day"))
            and (start_date is None or str(row.get("date")) >= start_date)
            and (end_date is None or str(row.get("date")) <= end_date)
        }
    )
    return dates


def _next_trading_date_map(trading_calendar: list[dict[str, Any]]) -> dict[str, str]:
    trading_dates = _trading_dates(trading_calendar)
    return {
        trading_dates[index]: trading_dates[index + 1]
        for index in range(len(trading_dates) - 1)
    }


def _trading_dates_by_market(trading_calendar: list[dict[str, Any]]) -> dict[str, set[str]]:
    dates_by_market: dict[str, set[str]] = {}
    for row in trading_calendar:
        if not row.get("date") or not bool(row.get("is_trading_day")):
            continue
        market = row.get("market")
        if not market:
            continue
        dates_by_market.setdefault(str(market), set()).add(str(row.get("date")))
    return dates_by_market


def _next_trading_date_by_market(trading_calendar: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    rows_by_market: dict[str, list[dict[str, Any]]] = {}
    for row in trading_calendar:
        market = row.get("market")
        if not market:
            continue
        rows_by_market.setdefault(str(market), []).append(row)
    return {
        market: _next_trading_date_map(rows)
        for market, rows in rows_by_market.items()
    }


def _effective_date_for_market(
    as_of_date: str,
    market: Any,
    *,
    market_next_trading_date: dict[str, dict[str, str]],
    market_trading_dates: dict[str, set[str]],
    global_next_trading_date: dict[str, str],
) -> str | None:
    market_value = str(market).strip() if market else None
    if market_value:
        market_dates = market_trading_dates.get(market_value)
        if market_dates is None:
            return None
        if as_of_date not in market_dates:
            return None
        return market_next_trading_date.get(market_value, {}).get(as_of_date)
    return global_next_trading_date.get(as_of_date)


def _rolling_adv20(daily_prices: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    prices = sorted(
        (
            row
            for row in daily_prices
            if row.get("date") is not None and row.get("ticker") is not None
        ),
        key=lambda row: (str(row.get("ticker")), str(row.get("date"))),
    )
    window_by_ticker: dict[str, list[float]] = {}
    adv_by_key: dict[tuple[str, str], float] = {}
    for row in prices:
        ticker = str(row.get("ticker"))
        date = str(row.get("date"))
        traded_value = float(row.get("traded_value") or 0.0)
        window = window_by_ticker.setdefault(ticker, [])
        window.append(traded_value)
        if len(window) > 20:
            window.pop(0)
        adv_by_key[(date, ticker)] = sum(window) / len(window)
    return adv_by_key

```


## FILE: tests\test_historical_security_panel.py
```
from data_analysts.security_panel import build_historical_security_panel, build_security_panel


def test_historical_security_panel_uses_next_trading_day_as_effective_date():
    rows, diag = build_historical_security_panel(
        daily_prices=[
            {
                "date": "2025-01-02",
                "ticker": "2330",
                "close": 100,
                "adj_close": 100,
                "volume": 10,
                "traded_value": 1000,
                "market_cap": 10000,
                "data_cutoff_at": "2025-01-02T00:00:00Z",
            },
            {
                "date": "2025-01-03",
                "ticker": "2330",
                "close": 101,
                "adj_close": 101,
                "volume": 11,
                "traded_value": 1111,
                "market_cap": 10100,
                "data_cutoff_at": "2025-01-03T00:00:00Z",
            },
        ],
        security_master=[
            {
                "ticker": "2330",
                "stock_name": "TSMC",
                "market": "TWSE",
                "listed": True,
                "security_type": "common_stock",
                "data_cutoff_at": "2025-01-01T00:00:00Z",
            }
        ],
        trading_calendar=[
            {"date": "2025-01-02", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
        start_date="2025-01-02",
        end_date="2025-01-03",
    )

    by_date = {row["as_of_date"]: row for row in rows}
    assert by_date["2025-01-02"]["effective_date"] == "2025-01-03"
    assert by_date["2025-01-03"]["effective_date"] == "2025-01-06"
    assert by_date["2025-01-03"]["source_max_date"] == "2025-01-03"
    assert diag["as_of_date_count"] == 2
    assert diag["duplicate_as_of_ticker_count"] == 0


def test_historical_security_panel_adv20_uses_only_past_and_current_values():
    rows, _ = build_historical_security_panel(
        daily_prices=[
            {
                "date": "2025-01-02",
                "ticker": "2330",
                "close": 100,
                "adj_close": 100,
                "volume": 1,
                "traded_value": 10,
                "market_cap": 10000,
            },
            {
                "date": "2025-01-03",
                "ticker": "2330",
                "close": 101,
                "adj_close": 101,
                "volume": 1,
                "traded_value": 30,
                "market_cap": 10100,
            },
        ],
        security_master=[
            {"ticker": "2330", "market": "TWSE", "listed": True, "security_type": "common_stock"}
        ],
        trading_calendar=[
            {"date": "2025-01-02", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
    )

    by_date = {row["as_of_date"]: row for row in rows}
    assert by_date["2025-01-02"]["adv20"] == 10
    assert by_date["2025-01-03"]["adv20"] == 20


def test_historical_security_panel_uses_market_specific_next_trading_day():
    rows, _ = build_historical_security_panel(
        daily_prices=[
            {
                "date": "2025-01-03",
                "ticker": "2330",
                "close": 101,
                "adj_close": 101,
                "volume": 10,
                "traded_value": 1000,
                "market_cap": 10000,
            },
            {
                "date": "2025-01-03",
                "ticker": "8088",
                "close": 51,
                "adj_close": 51,
                "volume": 10,
                "traded_value": 500,
                "market_cap": 5000,
            },
        ],
        security_master=[
            {"ticker": "2330", "stock_name": "TSMC", "market": "TWSE", "listed": True},
            {"ticker": "8088", "stock_name": "ABC", "market": "TPEX", "listed": True},
        ],
        trading_calendar=[
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-03", "market": "TPEX", "is_trading_day": True},
            {"date": "2025-01-07", "market": "TPEX", "is_trading_day": True},
        ],
        daily_tradability=[],
        start_date="2025-01-03",
        end_date="2025-01-03",
    )

    by_ticker = {row["ticker"]: row for row in rows}
    assert by_ticker["2330"]["effective_date"] == "2025-01-06"
    assert by_ticker["8088"]["effective_date"] == "2025-01-07"


def test_historical_security_panel_known_market_does_not_fallback_to_global_calendar():
    rows, diag = build_historical_security_panel(
        daily_prices=[
            {
                "date": "2025-01-03",
                "ticker": "2330",
                "close": 101,
                "adj_close": 101,
                "volume": 10,
                "traded_value": 1000,
                "market_cap": 10000,
            }
        ],
        security_master=[
            {"ticker": "2330", "stock_name": "TSMC", "market": "TWSE", "listed": True}
        ],
        trading_calendar=[
            {"date": "2025-01-03", "market": "TPEX", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TPEX", "is_trading_day": True},
            {"date": "2025-01-06", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
        start_date="2025-01-03",
        end_date="2025-01-03",
    )

    assert rows == [
        {
            "as_of_date": "2025-01-03",
            "effective_date": None,
            "source_max_date": "2025-01-03",
            "ticker": "2330",
            "stock_name": "TSMC",
            "market": "TWSE",
            "security_type": None,
            "listed": True,
            "tradable": True,
            "close": 101,
            "adj_close": 101,
            "traded_value": 1000.0,
            "market_cap": 10000,
            "adv20": 1000.0,
            "data_cutoff_at": None,
        }
    ]
    assert diag["effective_date_null_count"] == 1


def test_historical_security_panel_counts_duplicate_price_rows_in_diagnostics():
    rows, diag = build_historical_security_panel(
        daily_prices=[
            {
                "date": "2025-01-02",
                "ticker": "2330",
                "close": 100,
                "adj_close": 100,
                "volume": 10,
                "traded_value": 1000,
                "market_cap": 10000,
            },
            {
                "date": "2025-01-02",
                "ticker": "2330",
                "close": 999,
                "adj_close": 999,
                "volume": 99,
                "traded_value": 9999,
                "market_cap": 99999,
            },
        ],
        security_master=[{"ticker": "2330", "market": "TWSE", "listed": True}],
        trading_calendar=[
            {"date": "2025-01-02", "market": "TWSE", "is_trading_day": True},
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
        start_date="2025-01-02",
        end_date="2025-01-02",
    )

    assert len(rows) == 1
    assert rows[0]["close"] == 100
    assert diag["duplicate_as_of_ticker_count"] == 1


def test_historical_security_panel_effective_date_null_count_only_counts_emitted_rows():
    rows, diag = build_historical_security_panel(
        daily_prices=[
            {
                "date": "2025-01-03",
                "ticker": "2330",
                "close": 101,
                "adj_close": 101,
                "volume": 10,
                "traded_value": 1000,
                "market_cap": 10000,
            }
        ],
        security_master=[
            {"ticker": "2330", "market": "TWSE", "listed": True},
            {"ticker": "2317", "market": "TWSE", "listed": True},
        ],
        trading_calendar=[
            {"date": "2025-01-03", "market": "TWSE", "is_trading_day": True},
        ],
        daily_tradability=[],
        start_date="2025-01-03",
        end_date="2025-01-03",
    )

    assert rows == [
        {
            "as_of_date": "2025-01-03",
            "effective_date": None,
            "source_max_date": "2025-01-03",
            "ticker": "2330",
            "stock_name": None,
            "market": "TWSE",
            "security_type": None,
            "listed": True,
            "tradable": True,
            "close": 101,
            "adj_close": 101,
            "traded_value": 1000.0,
            "market_cap": 10000,
            "adv20": 1000.0,
            "data_cutoff_at": None,
        }
    ]
    assert diag["effective_date_null_count"] == 1


def test_build_security_panel_latest_helper_keeps_existing_latest_only_behavior():
    effective_date, rows = build_security_panel(
        daily_prices=[
            {"date": "2025-01-02", "ticker": "2330", "close": 100, "adj_close": 99, "volume": 10, "traded_value": 1000, "market_cap": 10000},
            {"date": "2025-01-03", "ticker": "2330", "close": 101, "adj_close": 100, "volume": 11, "traded_value": 1100, "market_cap": 10100},
        ],
        security_master=[
            {"ticker": "2330", "stock_name": "TSMC", "market": "TWSE", "listed": True, "security_type": "common_stock"}
        ],
    )

    assert effective_date == "2025-01-03"
    assert rows == [
        {
            "as_of_date": "2025-01-03",
            "source_max_date": "2025-01-03",
            "ticker": "2330",
            "stock_name": "TSMC",
            "market": "TWSE",
            "security_type": "common_stock",
            "listed": True,
            "tradable": True,
            "close": 101,
            "adj_close": 100,
            "traded_value": 1100.0,
            "market_cap": 10100,
            "adv20": 1100.0,
            "data_cutoff_at": None,
        }
    ]

```

