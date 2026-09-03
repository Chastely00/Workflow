"""Canonical, read-only materialization for the APISTOCK security snapshot."""

from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any


def materialize_security_master_rows(
    source_rows: list[dict[str, Any]], *, data_cutoff_at: str
) -> list[dict[str, Any]]:
    """Preserve APISTOCK identity fields and derive the latest listed market.

    APISTOCK is a user-authorized lifecycle snapshot.  ``tse_date`` and
    ``otc_date`` are effective listing dates, so the later non-null date is
    the observable current market without consulting a later APISTKATTR row.
    """
    by_ticker: dict[str, dict[str, Any]] = {}
    for source in source_rows:
        ticker = _required_text(source, "coid")
        if ticker in by_ticker:
            raise ValueError(f"duplicate APISTOCK ticker: {ticker}")
        tse_date = _optional_date(source.get("tse_date"), "tse_date")
        otc_date = _optional_date(source.get("otc_date"), "otc_date")
        market = _market(tse_date, otc_date)
        main_industry_c = _text(source.get("main_ind_c"))
        main_industry_e = _text(source.get("main_ind_e"))
        sub_industry_c = _text(source.get("sub_ind_c"))
        sub_industry_e = _text(source.get("sub_ind_e"))
        by_ticker[ticker] = {
            "ticker": ticker,
            "stock_name": _text(source.get("stk_name")),
            "english_name": _text(source.get("enm")),
            "list_date": _required_date(source.get("list_date"), "list_date"),
            "delist_date": _optional_date(source.get("delist_date"), "delist_date"),
            "market": market,
            "market_identity_source": (
                "APISTOCK_NO_BOARD_DATE_EMERGING"
                if market == "EMERGING"
                else "APISTOCK_TSE_OTC_DATE"
            ),
            "tse_date": tse_date,
            "otc_date": otc_date,
            "main_industry": main_industry_e,
            "sub_industry": sub_industry_e,
            "main_industry_c": main_industry_c,
            "main_industry_e": main_industry_e,
            "sub_industry_c": sub_industry_c,
            "sub_industry_e": sub_industry_e,
            "source_collection": "APISTOCK",
            "source_row_id": f"APISTOCK:{ticker}",
            "source_dataset_id": "security_master",
            "data_cutoff_at": data_cutoff_at,
        }
    return [by_ticker[ticker] for ticker in sorted(by_ticker)]


def _market(tse_date: str | None, otc_date: str | None) -> str | None:
    if tse_date is None and otc_date is None:
        return "EMERGING"
    if otc_date is None or (tse_date is not None and tse_date > otc_date):
        return "TWSE"
    if tse_date is None or otc_date > tse_date:
        return "TPEX"
    raise ValueError(f"ambiguous APISTOCK market dates: tse_date={tse_date}, otc_date={otc_date}")


def _required_text(row: dict[str, Any], field: str) -> str:
    value = _text(row.get(field))
    if not value:
        raise ValueError(f"APISTOCK requires {field}")
    return value


def _text(value: Any) -> str:
    return "" if _is_blank(value) else str(value).strip()


def _required_date(value: Any, field: str) -> str:
    result = _optional_date(value, field)
    if result is None:
        raise ValueError(f"APISTOCK requires {field}")
    return result


def _optional_date(value: Any, field: str) -> str | None:
    if _is_blank(value):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid APISTOCK {field}: {value!r}") from exc


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or str(value).strip().lower() in {
        "", "nan", "nat", "none",
    }
