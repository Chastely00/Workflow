from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from typing import Any


_RAW_FLAGS = (
    "atten_fg", "disp_fg", "full_fg", "limit_fg", "limo_fg", "sbadt_fg",
    "ssadt_fg", "susp_fg",
)


def build_daily_market_state_rows(
    *,
    trading_calendar_rows: list[dict[str, Any]],
    price_rows: list[dict[str, Any]],
    security_master_rows: list[dict[str, Any]],
    attribute_rows: list[dict[str, Any]],
    manifest_hashes: dict[str, str],
    build_start: str,
    build_end: str,
    data_cutoff_at: str,
    certified_source_start: str | None = None,
) -> list[dict[str, Any]]:
    """Classify TEJ daily market states without using future price observations.

    Equity lifecycle is supplied by the user-approved security-master snapshot.
    A missing APIPRCD and APISTKATTR pair inside an active lifecycle is an
    authorized halt with zero traded value; a delisted date is exclusive.
    """
    _require_manifest_hashes(manifest_hashes)
    sessions = sorted({
        _date_text(row["date"], "calendar.date")
        for row in trading_calendar_rows
        if row.get("is_trading_day", True) is not False
    })
    scoped_sessions = [day for day in sessions if build_start <= day <= build_end]
    if not scoped_sessions:
        raise ValueError("daily_market_state requested scope has no trading sessions")
    session_index = {day: index for index, day in enumerate(sessions)}
    master = _security_master_by_ticker(security_master_rows)
    prices = _unique_by_date_ticker(price_rows, "price")
    attributes = _unique_by_date_ticker(attribute_rows, "attribute")
    prices_by_day = _rows_by_day(prices)
    attributes_by_day = _rows_by_day(attributes)
    attribute_identity = _attribute_identity_before(attributes, build_start)
    rows: list[dict[str, Any]] = []

    for day in scoped_sessions:
        # APISKTATTR identity is strictly as-of: retain the latest observation
        # at or before this session, never back-fill from a later session.
        for ticker, attribute in attributes_by_day.get(day, {}).items():
            attribute_identity[ticker] = _merge_identity(attribute_identity.get(ticker), attribute)
        active_equities = {
            ticker: item for ticker, item in master.items()
            if item["list_date"] <= day and (item["delist_date"] is None or day < item["delist_date"])
        }
        tickers = set(active_equities)
        day_prices = prices_by_day.get(day, {})
        day_attributes = attributes_by_day.get(day, {})
        # Every daily_price_volume key must have an explicit state row.  A
        # security-master member can still be outside its effective lifecycle
        # (pre-list or on/after its exclusive delist date); omitting it breaks
        # coverage validation and hides a source/lifecycle conflict.
        tickers.update(day_prices)
        if not tickers:
            continue
        next_session = _next_session(day, sessions, session_index)
        for ticker in sorted(tickers):
            price = day_prices.get(ticker)
            attribute = day_attributes.get(ticker)
            lifecycle = active_equities.get(ticker)
            if lifecycle is None:
                if price is None:
                    continue
                master_lifecycle = master.get(ticker)
                if master_lifecycle is not None:
                    rows.append(_outside_lifecycle_row(
                        day, ticker, price, attribute, master_lifecycle,
                        next_session, manifest_hashes, data_cutoff_at,
                    ))
                    continue
                rows.append(_index_row(day, ticker, price, attribute, next_session, manifest_hashes, data_cutoff_at))
                continue
            identity = attribute_identity.get(ticker)
            if identity is None:
                if price is None:
                    # No price and no identity as-of evidence: emitting an
                    # equity state would require inventing market/type or
                    # reading a later APISKTATTR row.  It cannot be selected
                    # or traded, so exclude it from physical coverage.
                    continue
                if lifecycle.get("market") not in {"TWSE", "TPEX"}:
                    # The user-authorized lifecycle snapshot has no listed
                    # market and the as-of attribute has no identity.  Do not
                    # manufacture a tradeable TWSE/TPEX row, but retain a
                    # non-tradeable state so price/DMS key coverage is exact.
                    rows.append(_unidentified_lifecycle_row(
                        day, ticker, price, attribute, lifecycle, next_session,
                        manifest_hashes, build_start, build_end,
                        certified_source_start or build_start, data_cutoff_at,
                    ))
                    continue
                identity = {"mkt": lifecycle["market"], "stktp_e": "UNCLASSIFIED"}
            elif not str(identity.get("mkt") or "").strip():
                if lifecycle.get("market") not in {"TWSE", "TPEX"}:
                    # The attribute identifies a product type but not an
                    # exchange; retain a non-tradeable state rather than
                    # inventing a listed market.
                    rows.append(_unidentified_lifecycle_row(
                        day, ticker, price, attribute, lifecycle, next_session,
                        manifest_hashes, build_start, build_end,
                        certified_source_start or build_start, data_cutoff_at,
                    ))
                    continue
                identity = {**identity, "mkt": lifecycle["market"]}
            rows.append(_equity_row(
                day, ticker, price, attribute, identity, lifecycle, next_session, manifest_hashes,
                build_start, build_end, certified_source_start or build_start, data_cutoff_at,
                identity_source=(
                    "SECURITY_MASTER_SNAPSHOT"
                    if attribute_identity.get(ticker) is None
                    else "SECURITY_MASTER_SNAPSHOT_APISTKATTR_IDENTITY"
                ),
            ))
    return rows


def _equity_row(day: str, ticker: str, price: dict[str, Any] | None, attribute: dict[str, Any] | None,
                identity: dict[str, Any],
                lifecycle: dict[str, str | None], next_session: str, hashes: dict[str, str],
                build_start: str, build_end: str, certified_source_start: str, cutoff: str,
                identity_source: str = "SECURITY_MASTER_SNAPSHOT_APISTKATTR_IDENTITY") -> dict[str, Any]:
    present = price is not None
    amount = _amount(price) if present else None
    suspended = _flag(attribute, "susp_fg")
    if present and amount is None:
        reason, state, amount_state, value, zero, tradable = (
            "APIPRCD_INVALID_AMOUNT", "MISSING", "MISSING", None, False, None)
    elif present and suspended:
        reason, state, amount_state, value, zero, tradable = (
            "APISTKATTR_SUSPENSION_WITH_OBSERVED_AMOUNT", "HALTED", "OBSERVED", amount, False, False)
    elif present:
        reason, state, amount_state, value, zero, tradable = (
            "APIPRCD_OBSERVED_AMOUNT", "TRADING", "OBSERVED", amount, False, True)
    elif suspended:
        reason, state, amount_state, value, zero, tradable = (
            "APISTKATTR_SUSPENSION_NO_PRICE", "HALTED", "ZERO_AUTHORIZED", 0.0, True, False)
    elif attribute is not None:
        reason, state, amount_state, value, zero, tradable = (
            "APISTKATTR_NON_SUSPENSION_WITHOUT_PRICE", "MISSING", "MISSING", None, False, None)
    else:
        reason, state, amount_state, value, zero, tradable = (
            "ACTIVE_LIFECYCLE_DUAL_SOURCE_ABSENCE", "HALTED", "ZERO_AUTHORIZED", 0.0, True, False)
    interval_start = max(str(lifecycle["list_date"]), build_start, certified_source_start)
    delist = lifecycle["delist_date"]
    interval_end = min(str(delist), _day_after(build_end)) if delist else _day_after(build_end)
    return _base_row(day, ticker, price, attribute, next_session, hashes, cutoff) | {
        "market": _market(identity), "market_state": state, "state_reason": reason,
        "amount_state": amount_state, "authoritative_traded_value": value,
        "amount_zero_authorized": zero, "exchange_tradable": tradable,
        "instrument_kind": _instrument_kind(identity),
        "identity_source": identity_source,
        "security_master_market": _market(identity), "lifecycle_list_date": lifecycle["list_date"],
        "lifecycle_delist_date": delist, "lifecycle_interval_start": interval_start,
        "lifecycle_interval_end_exclusive": interval_end, "lifecycle_active": True,
        "lifecycle_conflict": False, "identity_conflict": False,
        "lifecycle_pit_status": "SNAPSHOT_EFFECTIVE_DATE_USER_AUTHORIZED",
        "revision_pit_status": "PIT_REVISION_UNVERIFIED",
    }


def _index_row(day: str, ticker: str, price: dict[str, Any], attribute: dict[str, Any] | None,
               next_session: str, hashes: dict[str, str], cutoff: str) -> dict[str, Any]:
    amount = _amount(price)
    if amount is None:
        raise ValueError(f"index {ticker} has invalid APIPRCD traded_value on {day}")
    return _base_row(day, ticker, price, attribute, next_session, hashes, cutoff) | {
        "market": "INDEX", "market_state": "TRADING", "state_reason": "APIPRCD_OBSERVED_AMOUNT",
        "amount_state": "OBSERVED", "authoritative_traded_value": amount,
        "amount_zero_authorized": False, "exchange_tradable": True,
        "instrument_kind": "INDEX", "identity_source": "APIPRCD_PRICE_ROW",
        "security_master_market": None, "lifecycle_list_date": None, "lifecycle_delist_date": None,
        "lifecycle_interval_start": None, "lifecycle_interval_end_exclusive": None,
        "lifecycle_active": False, "lifecycle_conflict": False, "identity_conflict": False,
        "lifecycle_pit_status": "SNAPSHOT_EFFECTIVE_DATE_USER_AUTHORIZED",
        "revision_pit_status": "PIT_REVISION_UNVERIFIED",
    }


def _unidentified_lifecycle_row(
    day: str, ticker: str, price: dict[str, Any] | None, attribute: dict[str, Any] | None,
    lifecycle: dict[str, str | None], next_session: str, hashes: dict[str, str],
    build_start: str, build_end: str, certified_source_start: str, cutoff: str,
) -> dict[str, Any]:
    interval_start = max(str(lifecycle["list_date"]), build_start, certified_source_start)
    delist = lifecycle["delist_date"]
    interval_end = min(str(delist), _day_after(build_end)) if delist else _day_after(build_end)
    lifecycle_market = lifecycle.get("market") or "UNKNOWN"
    is_emerging = lifecycle_market == "EMERGING"
    return _base_row(day, ticker, price, attribute, next_session, hashes, cutoff) | {
        "market": lifecycle_market, "market_state": "MISSING",
        "state_reason": (
            "LIFECYCLE_EMERGING_BOARD" if is_emerging else "LIFECYCLE_MARKET_UNIDENTIFIED"
        ), "amount_state": "MISSING",
        "authoritative_traded_value": None, "amount_zero_authorized": False,
        "exchange_tradable": None, "instrument_kind": "OTHER",
        "identity_source": "SECURITY_MASTER_SNAPSHOT",
        "security_master_market": lifecycle_market if is_emerging else None,
        "lifecycle_list_date": lifecycle["list_date"], "lifecycle_delist_date": delist,
        "lifecycle_interval_start": interval_start,
        "lifecycle_interval_end_exclusive": interval_end, "lifecycle_active": True,
        "lifecycle_conflict": False, "identity_conflict": False,
        "lifecycle_pit_status": "SNAPSHOT_EFFECTIVE_DATE_USER_AUTHORIZED",
        "revision_pit_status": "PIT_REVISION_UNVERIFIED",
    }


def _outside_lifecycle_row(
    day: str, ticker: str, price: dict[str, Any], attribute: dict[str, Any] | None,
    lifecycle: dict[str, str | None], next_session: str, hashes: dict[str, str],
    cutoff: str,
) -> dict[str, Any]:
    """Retain an observed price key that conflicts with the master lifecycle."""
    return _base_row(day, ticker, price, attribute, next_session, hashes, cutoff) | {
        "market": lifecycle.get("market") or "UNKNOWN",
        "market_state": "MISSING",
        "state_reason": "LIFECYCLE_OUTSIDE_ACTIVE_INTERVAL",
        "amount_state": "MISSING",
        "authoritative_traded_value": None,
        "amount_zero_authorized": False,
        "exchange_tradable": False,
        "instrument_kind": "OTHER",
        "identity_source": "SECURITY_MASTER_SNAPSHOT",
        "security_master_market": lifecycle.get("market"),
        "lifecycle_list_date": lifecycle["list_date"],
        "lifecycle_delist_date": lifecycle["delist_date"],
        "lifecycle_interval_start": lifecycle["list_date"],
        "lifecycle_interval_end_exclusive": lifecycle["delist_date"],
        "lifecycle_active": False,
        "lifecycle_conflict": True,
        "identity_conflict": False,
        "lifecycle_pit_status": "SNAPSHOT_EFFECTIVE_DATE_USER_AUTHORIZED",
        "revision_pit_status": "PIT_REVISION_UNVERIFIED",
    }


def _base_row(day: str, ticker: str, price: dict[str, Any] | None, attribute: dict[str, Any] | None,
              next_session: str, hashes: dict[str, str], cutoff: str) -> dict[str, Any]:
    values: dict[str, Any] = {
        "date": day, "ticker": ticker, "price_row_present": price is not None,
        "attr_row_present": attribute is not None,
        "full_delivery": _flag(attribute, "full_fg") if attribute is not None else None,
        "observation_date": day, "source_available_date": day,
        "availability_precision": "AFTER_CLOSE_DATE_ONLY", "earliest_execution_session": next_session,
        "security_master_manifest_sha256": hashes["security_master"],
        "calendar_manifest_sha256": hashes["trading_calendar"],
        "price_manifest_sha256": hashes["daily_price_volume"],
        "tradability_manifest_sha256": hashes["daily_tradability"],
        "classification_policy_version": "daily_market_state_v3", "data_cutoff_at": cutoff,
    }
    for flag in _RAW_FLAGS:
        values[flag] = _flag_text(attribute, flag) if attribute is not None else None
    return values


def _security_master_by_ticker(rows: list[dict[str, Any]]) -> dict[str, dict[str, str | None]]:
    result: dict[str, dict[str, str | None]] = {}
    for row in rows:
        ticker = str(row["ticker"])
        if ticker in result:
            raise ValueError(f"duplicate security_master ticker: {ticker}")
        market = str(row.get("market") or "").upper()
        result[ticker] = {"market": market, "list_date": _date_text(row["list_date"], "list_date"),
                          "delist_date": _optional_date(row.get("delist_date"), "delist_date")}
    return result


def _unique_by_date_ticker(rows: list[dict[str, Any]], source: str) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (_date_text(row["date"], f"{source}.date"), str(row["ticker"]))
        if key in result:
            raise ValueError(f"duplicate {source} date-ticker key: {key}")
        result[key] = row
    return result


def _rows_by_day(
    rows: dict[tuple[str, str], dict[str, Any]]
) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for (day, ticker), row in rows.items():
        result.setdefault(day, {})[ticker] = row
    return result


def _attribute_identity_before(
    attributes: dict[tuple[str, str], dict[str, Any]], build_start: str
) -> dict[str, dict[str, Any]]:
    """Return the final known APISKTATTR identity strictly before build_start."""
    result: dict[str, tuple[str, dict[str, Any]]] = {}
    for (day, ticker), row in attributes.items():
        if day >= build_start:
            continue
        current = result.get(ticker)
        if current is None or day > current[0]:
            prior = None if current is None else current[1]
            result[ticker] = (day, _merge_identity(prior, row))
    return {ticker: value[1] for ticker, value in result.items()}


def _merge_identity(
    prior: dict[str, Any] | None, current: dict[str, Any]
) -> dict[str, Any]:
    """Keep a prior as-of market only when the current TEJ market token is blank."""
    if str(current.get("mkt") or "").strip():
        return current
    if prior is None or not str(prior.get("mkt") or "").strip():
        return current
    return {**current, "mkt": prior["mkt"]}


def _amount(row: dict[str, Any] | None) -> float | None:
    value = None if row is None else row.get("traded_value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
        return None
    return float(value)


def _flag(row: dict[str, Any] | None, name: str) -> bool:
    return _flag_text(row, name) == "Y"


def _flag_text(row: dict[str, Any] | None, name: str) -> str | None:
    if row is None:
        return None
    value = row.get(name)
    if value is None:
        return "N"
    # TEJ flags are not uniformly boolean: e.g. limit_fg may be "+" or
    # "-".  Preserve its canonical source token; boolean consumers use
    # _flag(), which treats only Y as affirmative.
    return str(value).strip().upper() or "N"


def _market(attribute: dict[str, Any]) -> str:
    raw = str(attribute.get("mkt") or "").strip().upper()
    mapping = {"TSE": "TWSE", "TWSE": "TWSE", "OTC": "TPEX", "TPEX": "TPEX"}
    market = mapping.get(raw)
    if market is None:
        raise ValueError(f"invalid APISKTATTR market identity: {raw!r}")
    return market


def _instrument_kind(attribute: dict[str, Any]) -> str:
    text = str(attribute.get("stktp_e") or "").strip().upper()
    if "COMMON STOCK" in text:
        return "EQUITY"
    if text == "ETF":
        return "ETF"
    if text == "ETN":
        return "ETN"
    return "OTHER"


def _next_session(day: str, sessions: list[str], index: dict[str, int]) -> str:
    position = index[day] + 1
    if position >= len(sessions):
        raise ValueError(f"no next governed trading session after {day}")
    return sessions[position]


def _date_text(value: Any, field: str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and len(value.strip()) >= 10:
        try:
            return date.fromisoformat(value.strip()[:10]).isoformat()
        except ValueError as exc:
            raise ValueError(f"invalid {field}: {value!r}") from exc
    raise ValueError(f"invalid {field}: {value!r}")


def _optional_date(value: Any, field: str) -> str | None:
    return None if value is None or value == "" else _date_text(value, field)


def _day_after(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def _require_manifest_hashes(hashes: dict[str, str]) -> None:
    required = {"security_master", "trading_calendar", "daily_price_volume", "daily_tradability"}
    if set(hashes) != required or any(not isinstance(value, str) or len(value) != 64 for value in hashes.values()):
        raise ValueError("daily_market_state requires four manifest SHA-256 values")
