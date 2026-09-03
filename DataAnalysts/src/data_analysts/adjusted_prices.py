from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping

from data_analysts.adjusted_ohlc import (
    AdjustmentSeed,
    READY_ADJUSTMENT_STATUS,
)
from data_analysts.materialization import max_data_cutoff


class AdjustmentError(ValueError):
    """Raised when adjusted prices cannot be produced safely."""


def build_adjusted_daily_prices(
    rows: list[dict[str, Any]],
    *,
    dividend_events: list[dict[str, Any]] | None = None,
    capital_action_events: list[dict[str, Any]] | None = None,
    initial_state_by_ticker: Mapping[
        Any, AdjustmentSeed | Mapping[str, Any]
    ] | None = None,
    proven_new_series_tickers: set[Any] | None = None,
    require_seed: bool = False,
) -> list[dict[str, Any]]:
    adjustment_events = _price_adjustment_events(
        rows,
        dividend_events=dividend_events or [],
        capital_action_events=capital_action_events or [],
        initial_state_by_ticker=initial_state_by_ticker or {},
    )
    initial_state_by_ticker = initial_state_by_ticker or {}
    proven_new_series_tickers = proven_new_series_tickers or set()
    state_by_ticker: dict[Any, dict[str, float | None]] = {}
    adjusted: list[dict[str, Any]] = []

    for row in sorted(rows, key=lambda item: (str(item.get("ticker")), str(item.get("date")))):
        ticker = row.get("ticker")
        if ticker not in state_by_ticker:
            state_by_ticker[ticker] = _initial_state(
                ticker,
                initial_state_by_ticker=initial_state_by_ticker,
                proven_new_series_tickers=proven_new_series_tickers,
                require_seed=require_seed,
                price_date=row.get("date"),
            )
        state = state_by_ticker[ticker]
        event = adjustment_events.get((row.get("date"), ticker), {})
        event_adjustment = _event_adjustment(
            state, event, ticker=ticker, price_date=row.get("date")
        )
        adj_factor = float(state["adj_factor"]) * event_adjustment["factor_combined"]
        _require_positive_finite(adj_factor, "adjusted factor")

        output = dict(row)
        output["adj_factor"] = adj_factor
        output["cash_dividend"] = event_adjustment["cash_dividend"]
        output["stock_event_factor"] = event_adjustment["stock_event_factor"]
        output["cash_factor"] = event_adjustment["cash_factor"]
        output["factor_combined"] = event_adjustment["factor_combined"]
        output["price_adjustment_status"] = READY_ADJUSTMENT_STATUS
        lineage_cutoff = max_data_cutoff(
            state.get("data_cutoff_at"),
            row.get("data_cutoff_at"),
            event_adjustment.get("data_cutoff_at"),
        )
        if lineage_cutoff is not None:
            output["data_cutoff_at"] = lineage_cutoff
        for column in ("open", "high", "low", "close"):
            value = row.get(column)
            output[f"adj_{column}"] = None if value is None else float(value) * adj_factor

        state["adj_factor"] = adj_factor
        close = row.get("close")
        if close is not None:
            state["previous_close"] = float(close)
        state["last_materialized_date"] = row.get("date")
        if lineage_cutoff is not None:
            state["data_cutoff_at"] = lineage_cutoff
        adjusted.append(output)
    return adjusted


def _initial_state(
    ticker: Any,
    *,
    initial_state_by_ticker: Mapping[
        Any, AdjustmentSeed | Mapping[str, Any]
    ],
    proven_new_series_tickers: set[Any],
    require_seed: bool,
    price_date: Any,
) -> dict[str, float | None]:
    seed = initial_state_by_ticker.get(ticker)
    if seed is not None:
        if isinstance(seed, AdjustmentSeed):
            adj_factor_value = seed.adj_factor
            previous_close = seed.previous_close
            last_materialized_date = None
            data_cutoff_at = None
        elif isinstance(seed, Mapping):
            adj_factor_value = seed.get("adj_factor")
            previous_close = seed.get("previous_close", seed.get("prev_close"))
            last_materialized_date = seed.get("last_materialized_date")
            data_cutoff_at = seed.get("data_cutoff_at")
        else:
            raise AdjustmentError(f"invalid adjustment seed for ticker {ticker}")
        adj_factor = _require_positive_finite(
            adj_factor_value, "adjustment seed factor"
        )
        if previous_close is not None:
            try:
                previous_close = _require_positive_finite(
                    previous_close, "adjustment seed previous close"
                )
            except AdjustmentError as exc:
                raise AdjustmentError(
                    f"ticker={ticker} date={price_date} prev_close invalid: {exc}"
                ) from exc
        return {
            "adj_factor": adj_factor,
            "previous_close": previous_close,
            "last_materialized_date": last_materialized_date,
            "data_cutoff_at": data_cutoff_at,
        }

    if require_seed and ticker not in proven_new_series_tickers:
        raise AdjustmentError(f"missing verified adjustment seed for ticker {ticker}")
    return {
        "adj_factor": 1.0,
        "previous_close": None,
        "last_materialized_date": None,
        "data_cutoff_at": None,
    }


def _price_adjustment_events(
    price_rows: list[dict[str, Any]],
    *,
    dividend_events: list[dict[str, Any]],
    capital_action_events: list[dict[str, Any]],
    initial_state_by_ticker: Mapping[
        Any, AdjustmentSeed | Mapping[str, Any]
    ],
) -> dict[tuple[Any, Any], dict[str, Any]]:
    events: dict[tuple[Any, Any], dict[str, Any]] = {}
    first_price_date_by_ticker = _first_price_date_by_ticker(price_rows)
    traded_rows_by_ticker = _traded_price_rows_by_ticker(price_rows)
    for event in sorted(dividend_events, key=lambda row: (str(row.get("ticker")), str(row.get("event_date")))):
        ticker = event.get("ticker")
        event_date = event.get("event_date")
        cash_dividend = _event_amount(event.get("cash_dividend_per_share"), "cash dividend")
        stock_dividend_ratio = _event_amount(event.get("stock_dividend_ratio"), "stock dividend ratio")
        if _event_precedes_materialized_boundary(
            event_date,
            ticker,
            first_price_date_by_ticker,
            initial_state_by_ticker,
        ):
            continue
        target_date = _first_traded_date_at_or_after(traded_rows_by_ticker.get(ticker, []), event_date)
        if target_date is None:
            target_date = event_date
        output = events.setdefault((target_date, ticker), {})
        if cash_dividend:
            output["cash_dividend"] = output.get("cash_dividend", 0.0) + cash_dividend
        if stock_dividend_ratio:
            output["stock_event_factor"] = output.get("stock_event_factor", 1.0) * (1.0 + stock_dividend_ratio)
        output["data_cutoff_at"] = max_data_cutoff(
            output.get("data_cutoff_at"), event.get("data_cutoff_at")
        )

    for event in sorted(capital_action_events, key=lambda row: (str(row.get("ticker")), str(row.get("event_date")))):
        if event.get("action_type") != "stock_price_adjustment":
            continue
        reference = event.get("price_adjustment_reference")
        if reference is None:
            raise AdjustmentError("missing price_adjustment_reference")
        reference = _require_positive_finite(reference, "stock price adjustment factor")
        ticker = event.get("ticker")
        event_date = event.get("event_date")
        if _event_precedes_materialized_boundary(
            event_date,
            ticker,
            first_price_date_by_ticker,
            initial_state_by_ticker,
        ):
            continue
        target_date = _first_traded_date_at_or_after(traded_rows_by_ticker.get(ticker, []), event_date)
        output = events.setdefault((target_date or event_date, ticker), {})
        output["stock_event_factor"] = output.get("stock_event_factor", 1.0) * reference
        output["data_cutoff_at"] = max_data_cutoff(
            output.get("data_cutoff_at"), event.get("data_cutoff_at")
        )
    return events


def _event_adjustment(
    state: dict[str, Any],
    event: dict[str, Any],
    *,
    ticker: Any,
    price_date: Any,
) -> dict[str, Any]:
    stock_event_factor = event.get("stock_event_factor")
    if stock_event_factor is not None:
        _require_positive_finite(stock_event_factor, "stock event factor")
    factor = stock_event_factor if stock_event_factor is not None else 1.0
    cash_dividend = event.get("cash_dividend")
    cash_factor = 1.0
    if cash_dividend is not None:
        previous_close = state.get("previous_close")
        if previous_close is None:
            raise AdjustmentError(
                f"ticker={ticker} date={price_date} prev_close missing: "
                "missing_previous_close_for_cash_dividend"
            )
        if previous_close <= cash_dividend:
            raise AdjustmentError(
                f"ticker={ticker} date={price_date} prev_close={previous_close} "
                f"must exceed dividend={cash_dividend}: "
                "cash_dividend_exceeds_previous_close"
            )
        cash_factor = previous_close / (previous_close - cash_dividend)
        factor *= cash_factor
    _require_positive_finite(factor, "combined adjustment factor")
    return {
        "cash_dividend": cash_dividend,
        "stock_event_factor": stock_event_factor,
        "cash_factor": cash_factor,
        "factor_combined": factor,
        "data_cutoff_at": event.get("data_cutoff_at"),
    }


def _event_precedes_materialized_boundary(
    event_date: Any,
    ticker: Any,
    first_price_date_by_ticker: Mapping[Any, Any],
    initial_state_by_ticker: Mapping[
        Any, AdjustmentSeed | Mapping[str, Any]
    ],
) -> bool:
    first_price_date = first_price_date_by_ticker.get(ticker)
    if not _is_before(event_date, first_price_date):
        return False
    seed = initial_state_by_ticker.get(ticker)
    last_materialized_date = (
        seed.get("last_materialized_date")
        if isinstance(seed, Mapping)
        else None
    )
    return last_materialized_date is None or not _is_before(
        last_materialized_date, event_date
    )


def _event_amount(value: Any, label: str) -> float:
    if value is None:
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AdjustmentError(f"invalid {label}") from exc
    if not math.isfinite(parsed) or parsed < 0.0:
        raise AdjustmentError(f"invalid {label}")
    return parsed


def _require_positive_finite(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AdjustmentError(f"invalid {label}") from exc
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise AdjustmentError(f"invalid {label}")
    return parsed


def _traded_price_rows_by_ticker(price_rows: list[dict[str, Any]]) -> dict[Any, list[dict[str, Any]]]:
    rows_by_ticker: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in price_rows:
        if _is_traded_price_row(row):
            rows_by_ticker[row.get("ticker")].append(row)
    return {
        ticker: sorted(rows, key=lambda row: str(row.get("date")))
        for ticker, rows in rows_by_ticker.items()
    }


def _first_price_date_by_ticker(price_rows: list[dict[str, Any]]) -> dict[Any, Any]:
    first_dates: dict[Any, Any] = {}
    for row in sorted(price_rows, key=lambda item: (str(item.get("ticker")), str(item.get("date")))):
        ticker = row.get("ticker")
        if ticker not in first_dates:
            first_dates[ticker] = row.get("date")
    return first_dates


def _first_traded_date_at_or_after(rows: list[dict[str, Any]], event_date: Any) -> Any:
    if event_date is None:
        return None
    event_date_text = str(event_date)
    for row in rows:
        row_date = row.get("date")
        if row_date is not None and str(row_date) >= event_date_text:
            return row_date
    return None


def _is_before(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return str(left) < str(right)


def _is_traded_price_row(row: dict[str, Any]) -> bool:
    volume = row.get("volume")
    if volume is None:
        return True
    try:
        return float(volume) > 0.0
    except (TypeError, ValueError):
        return False
