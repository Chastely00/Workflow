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

