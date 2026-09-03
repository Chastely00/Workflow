from __future__ import annotations

from datetime import date, datetime
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
    master_by_ticker = {
        str(row["ticker"]): row
        for row in security_master
        if _ticker_value(row) is not None
    }
    tradability_by_key = {
        (row_date, row_ticker): row
        for row in daily_tradability or []
        if (row_date := _row_date(row, "date", "mdate", "source_date", "zdate"))
        and (row_ticker := _ticker_value(row))
    }
    price_rows_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    duplicate_count = 0
    for row in daily_prices:
        row_date = _row_date(row, "date", "source_date", "mdate", "zdate")
        row_ticker = _ticker_value(row)
        if not row_date or not row_ticker:
            continue
        key = (row_date, row_ticker)
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


def _ticker_value(row: dict[str, Any]) -> str | None:
    for field in ("ticker", "coid"):
        value = row.get(field)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _row_date(row: dict[str, Any], *fields: str) -> str | None:
    for field in fields:
        value = row.get(field)
        text = _date_text(value)
        if text:
            return text
    return None


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    return text[:10]


def _trading_dates(
    trading_calendar: list[dict[str, Any]],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[str]:
    dates = sorted(
        {
            row_date
            for row in trading_calendar
            if (row_date := _row_date(row, "date", "zdate", "source_date", "mdate"))
            and _is_trading_day(row)
            and (start_date is None or row_date >= start_date)
            and (end_date is None or row_date <= end_date)
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
        row_date = _row_date(row, "date", "zdate", "source_date", "mdate")
        if not row_date or not _is_trading_day(row):
            continue
        market = _market_value(row)
        if not market:
            continue
        dates_by_market.setdefault(market, set()).add(row_date)
    return dates_by_market


def _next_trading_date_by_market(trading_calendar: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    rows_by_market: dict[str, list[dict[str, Any]]] = {}
    for row in trading_calendar:
        market = _market_value(row)
        if not market:
            continue
        rows_by_market.setdefault(market, []).append(row)
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


def _market_value(row: dict[str, Any]) -> str | None:
    for field in ("market", "mkt"):
        value = row.get(field)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _is_trading_day(row: dict[str, Any]) -> bool:
    if "is_trading_day" in row:
        return bool(row.get("is_trading_day"))
    if "date_rmk" in row:
        return str(row.get("date_rmk") or "").strip() == ""
    return False


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
