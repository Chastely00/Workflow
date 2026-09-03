from __future__ import annotations

from typing import Any


CASH_DIVIDEND_FIELDS = (
    ("q1", "q1ex_date", "q1mt_div"),
    ("q2", "q2ex_date", "q2mt_div"),
    ("q3", "q3ex_date", "q3mt_div"),
    ("q4", "q4ex_date", "q4mt_div"),
)


def build_dividend_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for index, row in enumerate(rows):
        source_row_id = row.get("source_row_id", f"dividend_policy:{index}")
        if any(date_field in row or dividend_field in row for _, date_field, dividend_field in CASH_DIVIDEND_FIELDS):
            for quarter, date_field, dividend_field in CASH_DIVIDEND_FIELDS:
                ex_date = row.get(date_field)
                cash_dividend = _positive_float(row.get(dividend_field))
                if not ex_date or cash_dividend is None:
                    continue
                _upsert_dividend_event(
                    events_by_key,
                    key=(str(row.get("ticker")), "cash_dividend", str(ex_date), quarter),
                    event=_dividend_event(
                        row,
                        ex_date=str(ex_date),
                        cash_dividend_per_share=cash_dividend,
                        stock_dividend_ratio=0.0,
                        source_row_id=f"{source_row_id}:{quarter}",
                    ),
                )
            stock_ex_date = row.get("mex_date")
            stock_dividend = _positive_float(row.get("mt_mer"))
            if stock_ex_date and stock_dividend is not None:
                _upsert_dividend_event(
                    events_by_key,
                    key=(str(row.get("ticker")), "stock_dividend", str(stock_ex_date), ""),
                    event=_dividend_event(
                        row,
                        ex_date=str(stock_ex_date),
                        cash_dividend_per_share=0.0,
                        stock_dividend_ratio=stock_dividend / 10.0,
                        source_row_id=f"{source_row_id}:stock",
                    ),
                )
            continue

        ex_date = row.get("ex_date")
        _upsert_dividend_event(
            events_by_key,
            key=(str(row.get("ticker")), "dividend", str(ex_date), ""),
            event=_dividend_event(
                row,
                ex_date=ex_date,
                cash_dividend_per_share=row.get("cash_dividend_per_share", 0.0),
                stock_dividend_ratio=row.get("stock_dividend_ratio", 0.0),
                source_row_id=source_row_id,
            ),
        )
    return sorted(events_by_key.values(), key=lambda row: (str(row.get("event_date")), str(row.get("ticker")), str(row.get("source_row_id"))))


def _upsert_dividend_event(
    events_by_key: dict[tuple[str, str, str, str], dict[str, Any]],
    *,
    key: tuple[str, str, str, str],
    event: dict[str, Any],
) -> None:
    events_by_key[key] = event


def build_capital_action_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        source_row_id = row.get("source_row_id", f"capital_formation:{index}")
        event_date = row.get("ex_date") or row.get("event_date") or row.get("source_date")
        pct_dec1 = _positive_float(row.get("pct_dec1"))
        if event_date and pct_dec1 is not None:
            events.append(
                _capital_event(
                    row,
                    ex_date=str(event_date),
                    action_type="capital_reduction",
                    share_multiplier=1.0 - pct_dec1 / 100.0,
                    cash_return_per_share=_positive_float(row.get("ashback")) or 0.0,
                    price_adjustment_reference=None,
                    source_row_id=f"{source_row_id}:capital_reduction",
                )
            )

        slamt = _positive_float(row.get("slamt"))
        stk_join = _positive_float(row.get("stk_join"))
        if event_date and slamt is not None and stk_join is not None and stk_join != 0:
            events.append(
                _capital_event(
                    row,
                    ex_date=str(event_date),
                    action_type="split",
                    share_multiplier=slamt / stk_join,
                    cash_return_per_share=0.0,
                    price_adjustment_reference=None,
                    source_row_id=f"{source_row_id}:split",
                )
            )

        precls = _positive_float(row.get("precls"))
        exprice = _positive_float(row.get("exprice"))
        if event_date and precls is not None and exprice is not None and exprice != 0:
            events.append(
                _capital_event(
                    row,
                    ex_date=str(event_date),
                    action_type="stock_price_adjustment",
                    share_multiplier=1.0,
                    cash_return_per_share=0.0,
                    price_adjustment_reference=precls / exprice,
                    source_row_id=f"{source_row_id}:stock_price_adjustment",
                )
            )

        if not any(key in row for key in ("pct_dec1", "slamt", "stk_join", "precls", "exprice")):
            ex_date = row.get("ex_date")
            events.append(
                _capital_event(
                    row,
                    ex_date=ex_date,
                    action_type=row.get("action_type"),
                    share_multiplier=row.get("share_multiplier", 1.0),
                    cash_return_per_share=row.get("cash_return_per_share", 0.0),
                    price_adjustment_reference=row.get("price_adjustment_reference"),
                    source_row_id=source_row_id,
                )
            )
    return events


def build_corporate_actions(
    dividend_events: list[dict[str, Any]],
    capital_action_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for event in dividend_events:
        stock_dividend_ratio = float(event.get("stock_dividend_ratio") or 0.0)
        action_type = "stock_dividend" if stock_dividend_ratio > 0.0 else "cash_dividend"
        actions.append(
            {
                "event_date": event["event_date"],
                "ticker": event["ticker"],
                "action_type": action_type,
                "cash_amount": event["cash_dividend_per_share"],
                "share_multiplier": 1.0 + stock_dividend_ratio,
                "source_dataset_id": event["source_dataset_id"],
                "source_row_id": event["source_row_id"],
                "data_cutoff_at": event["data_cutoff_at"],
            }
        )
    for event in capital_action_events:
        if event.get("action_type") == "stock_price_adjustment":
            continue
        actions.append(
            {
                "event_date": event["event_date"],
                "ticker": event["ticker"],
                "action_type": event["action_type"],
                "cash_amount": event.get("cash_return_per_share", 0.0),
                "share_multiplier": event.get("share_multiplier", 1.0),
                "source_dataset_id": event["source_dataset_id"],
                "source_row_id": event["source_row_id"],
                "data_cutoff_at": event["data_cutoff_at"],
            }
        )
    return actions


def _dividend_event(
    row: dict[str, Any],
    *,
    ex_date: Any,
    cash_dividend_per_share: Any,
    stock_dividend_ratio: Any,
    source_row_id: Any,
) -> dict[str, Any]:
    return {
        "event_date": ex_date,
        "ex_date": ex_date,
        "ticker": row.get("ticker"),
        "cash_dividend_per_share": cash_dividend_per_share,
        "stock_dividend_ratio": stock_dividend_ratio,
        "source_dataset_id": "dividend_policy",
        "source_row_id": source_row_id,
        "data_cutoff_at": row.get("data_cutoff_at"),
    }


def _capital_event(
    row: dict[str, Any],
    *,
    ex_date: Any,
    action_type: Any,
    share_multiplier: Any,
    cash_return_per_share: Any,
    price_adjustment_reference: Any,
    source_row_id: Any,
) -> dict[str, Any]:
    return {
        "event_date": ex_date,
        "ex_date": ex_date,
        "ticker": row.get("ticker"),
        "action_type": action_type,
        "share_multiplier": share_multiplier,
        "cash_return_per_share": cash_return_per_share,
        "price_adjustment_reference": price_adjustment_reference,
        "source_dataset_id": "capital_formation",
        "source_row_id": source_row_id,
        "data_cutoff_at": row.get("data_cutoff_at"),
    }


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0.0:
        return None
    return parsed
