from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date, datetime, time
from typing import Any

from data_analysts.pit import PitError, normalize_date, select_latest_pit_rows


class RawFamilyError(ValueError):
    """Raised when a raw family cannot be normalized without data leakage or ambiguity."""


def normalize_raw_family(
    family_id: str,
    rows: list[dict[str, object]],
    pit_registry: dict[str, object],
    *,
    decision_dates: list[str] | None = None,
    selected_materialization: str = "snapshots",
) -> dict[str, object]:
    rule = _rule_for(family_id, pit_registry)
    normalizer = _NORMALIZERS.get(family_id)
    if normalizer is None:
        if family_id not in _GENERIC_MDATE_FAMILY_IDS:
            raise RawFamilyError(f"unsupported raw family normalizer for {family_id}")
        normalizer = _normalize_generic_mdate_family
    raw_rows, diagnostics = normalizer(family_id, rows, rule)
    selected_rows: list[dict[str, object]] = []
    if family_id == "financial_statement_raw":
        raw_timestamp_resolved_count = int(
            diagnostics.get("raw_same_day_source_timestamp_resolved_count", 0)
        )
        selected_rows, selected_diag = _selected_rows(
            raw_rows,
            selected_family_id="financial_statement_pit_selected",
            pit_registry=pit_registry,
            decision_dates=decision_dates,
            selected_materialization=selected_materialization,
        )
        diagnostics.update(selected_diag)
        diagnostics["resolved_same_day_source_timestamp_count"] = (
            raw_timestamp_resolved_count
            + int(selected_diag.get("resolved_same_day_source_timestamp_count", 0))
        )
        diagnostics["resolved_duplicate_count"] = (
            raw_timestamp_resolved_count
            + int(selected_diag.get("resolved_duplicate_count", 0))
        )
    elif family_id == "self_reported_numbers_raw":
        selected_rows, selected_diag = _selected_rows(
            raw_rows,
            selected_family_id="self_reported_numbers_pit_selected",
            pit_registry=pit_registry,
            decision_dates=decision_dates,
            selected_materialization=selected_materialization,
        )
        diagnostics.update(selected_diag)
    diagnostics.setdefault("source_row_count", len(rows))
    diagnostics.setdefault("published_row_count", len(raw_rows))
    diagnostics.setdefault("omitted_row_count", len(rows) - len(raw_rows))
    diagnostics.setdefault("pit_null_count", 0)
    diagnostics.setdefault("pit_parse_failure_count", 0)
    diagnostics.setdefault("duplicate_logical_key_count", _duplicate_count(raw_rows, list(rule["logical_key"])))
    diagnostics.setdefault("resolved_duplicate_count", 0)
    diagnostics.setdefault("unresolved_duplicate_count", 0)
    _add_date_range(diagnostics, raw_rows, "source_available_date")
    return {
        "family_id": family_id,
        "raw_rows": raw_rows,
        "selected_rows": selected_rows,
        "diagnostics": diagnostics,
    }


def _normalize_trading_calendar(
    family_id: str, rows: list[dict[str, object]], rule: dict[str, object]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    output = []
    for row in rows:
        date_value = _required_date(row, "zdate", family_id)
        market = str(row.get("mkt") or row.get("market") or "TWSE").strip()
        if not market:
            raise RawFamilyError("missing market for trading_calendar")
        date_rmk = "" if row.get("date_rmk") is None else str(row.get("date_rmk")).strip()
        output.append(
            _with_source_metadata(
                row,
                {
                    "date": date_value,
                    "market": market,
                    "is_trading_day": date_rmk == "",
                    "date_rmk": date_rmk,
                    "source_available_date": date_value,
                },
            )
        )
    return output, {
        "trading_day_count": sum(1 for row in output if row["is_trading_day"]),
        "non_trading_day_count": sum(1 for row in output if not row["is_trading_day"]),
    }


def _normalize_monthly_sales(
    family_id: str, rows: list[dict[str, object]], rule: dict[str, object]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    output = []
    for row in rows:
        output.append(
            _with_source_metadata(
                row,
                {
                    "ticker": _required_text(row, "coid", family_id),
                    "source_period_date": _required_date(row, "mdate", family_id),
                    "source_available_date": _required_date(row, "annd_s", family_id),
                },
            )
        )
    return output, {}


def _normalize_daily_panel(
    family_id: str, rows: list[dict[str, object]], rule: dict[str, object]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    output = []
    for row in rows:
        ticker = str(row.get("coid") or row.get("ticker") or "").strip()
        if not ticker:
            raise RawFamilyError(f"missing required field for {family_id}: coid/ticker")
        date_value = _required_date(row, "mdate", family_id)
        output.append(
            _with_source_metadata(
                row,
                {
                    "date": date_value,
                    "ticker": ticker,
                    "source_available_date": date_value,
                },
            )
        )
    return output, {}


def _normalize_financial_statement(
    family_id: str, rows: list[dict[str, object]], rule: dict[str, object]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    output = []
    by_no = Counter()
    for row in rows:
        no = _required_text(row, "no", family_id)
        by_no[no] += 1
        output.append(
            _with_source_metadata(
                row,
                {
                    "ticker": _required_text(row, "coid", family_id),
                    "no": no,
                    "sem": _required_text(row, "sem", family_id),
                    "curr": _required_text(row, "curr", family_id),
                    "merg": _required_text(row, "merg", family_id),
                    "period_start_date": _optional_date(row, "begd", family_id),
                    "period_end_date": _required_date(row, "endd", family_id),
                    "source_period_date": _optional_date(row, "mdate", family_id),
                    "source_available_date": _required_date(row, "key3", family_id),
                    "revision_date": _required_date(row, "mdate", family_id),
                },
            )
        )
    collapsed, resolved_count = _collapse_raw_same_day_timestamp_duplicates(
        output,
        logical_key=list(rule["logical_key"]),
        source_timestamp_field="key3",
    )
    return collapsed, {
        "rows_by_no": dict(by_no),
        "raw_same_day_source_timestamp_resolved_count": resolved_count,
    }


def _normalize_self_reported_numbers(
    family_id: str, rows: list[dict[str, object]], rule: dict[str, object]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    output = []
    by_key3 = Counter()
    for row in rows:
        key3 = _required_text(row, "key3", family_id)
        by_key3[key3] += 1
        output.append(
            _with_source_metadata(
                row,
                {
                    "ticker": _required_text(row, "coid", family_id),
                    "key3": key3,
                    "sem": _required_text(row, "sem", family_id),
                    "curr": _required_text(row, "curr", family_id),
                    "merg": _required_text(row, "merg", family_id),
                    "period_end_date": _required_date(row, "endd" if "endd" in row else "mdate", family_id),
                    "source_available_date": _required_date(row, "annd", family_id),
                    "revision_date": _required_date(row, "mdate", family_id),
                },
            )
        )
    return output, {"rows_by_key3": dict(by_key3)}


def _normalize_generic_mdate_family(
    family_id: str, rows: list[dict[str, object]], rule: dict[str, object]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    output = []
    for row in rows:
        output.append(
            _with_source_metadata(
                row,
                {
                    "ticker": str(row.get("coid") or row.get("ticker") or "").strip() or None,
                    "source_date": _required_date(row, "mdate", family_id),
                    "source_available_date": _required_date(row, "mdate", family_id),
                },
            )
        )
    return output, {}


def _normalize_futures_near_month(
    family_id: str, rows: list[dict[str, object]], rule: dict[str, object]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    output = []
    for row in rows:
        date_value = _required_date(row, "日期", family_id)
        output.append(
            _with_source_metadata(
                row,
                {
                    "date": date_value,
                    "source_available_date": date_value,
                    "contract": str(row.get("contract") or row.get("契約") or row.get("symbol") or "TX_1").strip(),
                },
            )
        )
    return output, {"duplicate_date_contract_count": _duplicate_count(output, ["date", "contract"])}


def _required_date(row: dict[str, object], field: str, family_id: str) -> str:
    if field not in row:
        raise RawFamilyError(f"missing required PIT field for {family_id}: {field}")
    try:
        value = normalize_date(row[field])
    except PitError as exc:
        raise RawFamilyError(f"invalid PIT date for {family_id}.{field}: {row[field]!r}") from exc
    if value is None:
        raise RawFamilyError(f"blank required PIT field for {family_id}: {field}")
    return value


def _required_text(row: dict[str, object], field: str, family_id: str) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise RawFamilyError(f"missing required field for {family_id}: {field}")
    return value


def _optional_date(row: dict[str, object], field: str, family_id: str) -> str | None:
    try:
        return normalize_date(row.get(field))
    except PitError as exc:
        raise RawFamilyError(f"invalid PIT date for {family_id}.{field}: {row.get(field)!r}") from exc


def _with_source_metadata(row: dict[str, object], canonical: dict[str, object]) -> dict[str, object]:
    output = dict(canonical)
    for key, value in row.items():
        if key not in output:
            output[key] = value
    return output


def _selected_rows(
    rows: list[dict[str, object]],
    *,
    selected_family_id: str,
    pit_registry: dict[str, object],
    decision_dates: list[str] | None,
    selected_materialization: str = "snapshots",
) -> tuple[list[dict[str, object]], dict[str, object]]:
    normalized_decision_dates = [_normalize_decision_date(decision_date) for decision_date in decision_dates or []]
    diagnostics: dict[str, object] = {
        "decision_date_count": len(normalized_decision_dates),
        "input_row_count": 0,
        "eligible_row_count": 0,
        "future_row_count": 0,
        "selected_row_count": 0,
        "resolved_duplicate_count": 0,
        "resolved_same_day_source_timestamp_count": 0,
        "unresolved_duplicate_count": 0,
        "selected_no_q_row_count": 0,
        "selected_key3_category_counts": {},
    }
    if not normalized_decision_dates:
        return [], diagnostics

    rule = _selected_rule_for(selected_family_id, pit_registry)
    counter_keys = {
        "input_row_count",
        "eligible_row_count",
        "future_row_count",
        "resolved_duplicate_count",
        "unresolved_duplicate_count",
    }
    if selected_materialization not in {"snapshots", "state_updates"}:
        raise RawFamilyError(
            f"unsupported selected materialization: {selected_materialization}"
        )
    selector = (
        _select_pit_state_updates_by_decision_dates
        if selected_materialization == "state_updates"
        else _select_latest_pit_rows_by_decision_dates
    )
    try:
        selected_rows, selected_diagnostics_by_date = selector(
            rows,
            logical_key=list(rule["logical_key"]),
            availability_field=str(rule["availability_field"]),
            revision_field=_optional_rule_text(rule.get("revision_field")),
            source_timestamp_field=_source_timestamp_field_for(selected_family_id, rule),
            decision_dates=normalized_decision_dates,
        )
    except PitError as exc:
        raise RawFamilyError(str(exc)) from exc

    for normalized_decision_date in normalized_decision_dates:
        selected_diag = selected_diagnostics_by_date[normalized_decision_date]
        for key in counter_keys:
            diagnostics[key] = int(diagnostics[key]) + int(selected_diag.get(key, 0))
        diagnostics["resolved_same_day_source_timestamp_count"] = int(
            diagnostics["resolved_same_day_source_timestamp_count"]
        ) + int(selected_diag.get("resolved_same_day_source_timestamp_count", 0))
    diagnostics["selected_row_count"] = len(selected_rows)
    diagnostics["selected_materialization"] = selected_materialization
    diagnostics["selected_no_q_row_count"] = sum(1 for row in selected_rows if row.get("no") == "Q")
    diagnostics["selected_key3_category_counts"] = dict(
        Counter(str(row["key3"]) for row in selected_rows if "key3" in row)
    )
    return selected_rows, diagnostics


def _select_pit_state_updates_by_decision_dates(
    rows: list[dict[str, object]],
    *,
    logical_key: list[str],
    availability_field: str,
    revision_field: str | None,
    source_timestamp_field: str | None,
    decision_dates: list[str],
) -> tuple[list[dict[str, object]], dict[str, dict[str, int]]]:
    """Emit only state changes effective at each requested decision date.

    A complete daily selected-PIT grid duplicates the same financial statement
    state on every trading day.  The event form is PIT-equivalent when a reader
    selects the latest event at or before its decision date, while keeping a
    full-history materialization bounded by source changes rather than days.
    """
    events_by_available, availability_counts, same_day_resolved_counts = (
        _selection_events_by_available(
            rows,
            logical_key=logical_key,
            availability_field=availability_field,
            revision_field=revision_field,
            source_timestamp_field=source_timestamp_field,
        )
    )
    unique_decision_dates = sorted(set(decision_dates))
    if not unique_decision_dates:
        return [], {}

    candidates_by_decision: dict[str, dict[tuple[str, ...], list[dict[str, object]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    eligible_row_count = 0
    same_day_resolved_count = 0
    for available, events in events_by_available.items():
        position = bisect_left(unique_decision_dates, available)
        if position == len(unique_decision_dates):
            continue
        decision_date = unique_decision_dates[position]
        eligible_row_count += availability_counts[available]
        same_day_resolved_count += same_day_resolved_counts.get(available, 0)
        for key, row in events:
            candidates_by_decision[decision_date][key].append(row)

    selected_rows: list[dict[str, object]] = []
    diagnostics_by_date: dict[str, dict[str, int]] = {}
    cumulative_eligible = 0
    cumulative_same_day_resolved = 0
    for decision_date in unique_decision_dates:
        candidates = candidates_by_decision.get(decision_date, {})
        selected_count = 0
        for key in sorted(candidates):
            rows_for_key = candidates[key]
            selected = _select_latest_active_row(
                key,
                rows_for_key,
                availability_field=availability_field,
                revision_field=revision_field,
            )
            selected_with_decision = dict(selected)
            selected_with_decision["decision_date"] = decision_date
            selected_rows.append(selected_with_decision)
            selected_count += 1
        for available, count in availability_counts.items():
            position = bisect_left(unique_decision_dates, available)
            if position < len(unique_decision_dates) and unique_decision_dates[position] == decision_date:
                cumulative_eligible += count
                cumulative_same_day_resolved += same_day_resolved_counts.get(available, 0)
        diagnostics_by_date[decision_date] = {
            "input_row_count": len(rows),
            "eligible_row_count": cumulative_eligible,
            "future_row_count": len(rows) - cumulative_eligible,
            "selected_row_count": selected_count,
            "resolved_duplicate_count": cumulative_eligible - len(selected_rows),
            "resolved_same_day_source_timestamp_count": cumulative_same_day_resolved,
            "unresolved_duplicate_count": 0,
        }
    return selected_rows, diagnostics_by_date


def _select_latest_pit_rows_by_decision_dates(
    rows: list[dict[str, object]],
    *,
    logical_key: list[str],
    availability_field: str,
    revision_field: str | None,
    source_timestamp_field: str | None,
    decision_dates: list[str],
) -> tuple[list[dict[str, object]], dict[str, dict[str, int]]]:
    events_by_available, availability_counts, same_day_resolved_counts = _selection_events_by_available(
        rows,
        logical_key=logical_key,
        availability_field=availability_field,
        revision_field=revision_field,
        source_timestamp_field=source_timestamp_field,
    )
    unique_decision_dates = sorted(set(decision_dates))
    available_dates = sorted(events_by_available)
    active_rows_by_key: dict[tuple[str, ...], list[dict[str, object]]] = defaultdict(list)
    selected_by_key: dict[tuple[str, ...], dict[str, object]] = {}
    snapshots: dict[str, list[dict[str, object]]] = {}
    diagnostics_by_date: dict[str, dict[str, int]] = {}
    available_index = 0
    eligible_row_count = 0
    same_day_resolved_count = 0

    for decision_date in unique_decision_dates:
        touched_keys: set[tuple[str, ...]] = set()
        while available_index < len(available_dates) and available_dates[available_index] <= decision_date:
            available = available_dates[available_index]
            eligible_row_count += availability_counts[available]
            same_day_resolved_count += same_day_resolved_counts.get(available, 0)
            for key, row in events_by_available[available]:
                active_rows_by_key[key].append(row)
                touched_keys.add(key)
            available_index += 1
        for key in touched_keys:
            selected_by_key[key] = _select_latest_active_row(
                key,
                active_rows_by_key[key],
                availability_field=availability_field,
                revision_field=revision_field,
            )
        selected = [selected_by_key[key] for key in sorted(selected_by_key)]
        snapshots[decision_date] = selected
        diagnostics_by_date[decision_date] = {
            "input_row_count": len(rows),
            "eligible_row_count": eligible_row_count,
            "future_row_count": len(rows) - eligible_row_count,
            "selected_row_count": len(selected),
            "resolved_duplicate_count": eligible_row_count - len(selected),
            "resolved_same_day_source_timestamp_count": same_day_resolved_count,
            "unresolved_duplicate_count": 0,
        }

    selected_rows: list[dict[str, object]] = []
    for decision_date in decision_dates:
        for row in snapshots[decision_date]:
            selected_with_decision = dict(row)
            selected_with_decision["decision_date"] = decision_date
            selected_rows.append(selected_with_decision)
    return selected_rows, diagnostics_by_date


def _selection_events_by_available(
    rows: list[dict[str, object]],
    *,
    logical_key: list[str],
    availability_field: str,
    revision_field: str | None,
    source_timestamp_field: str | None,
) -> tuple[
    dict[str, list[tuple[tuple[str, ...], dict[str, object]]]],
    dict[str, int],
    dict[str, int],
]:
    grouped: dict[tuple[tuple[str, ...], str, str], list[dict[str, object]]] = defaultdict(list)
    availability_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        available = _required_date(row, availability_field, "selected")
        revision = _required_date(row, revision_field, "selected") if revision_field else ""
        normalized = dict(row)
        normalized[availability_field] = available
        if revision_field:
            normalized[revision_field] = revision
        key = _logical_key(normalized, logical_key)
        grouped[(key, available, revision)].append(normalized)
        availability_counts[available] += 1

    events_by_available: dict[str, list[tuple[tuple[str, ...], dict[str, object]]]] = defaultdict(list)
    same_day_resolved_counts: dict[str, int] = defaultdict(int)
    for (key, available, _revision), candidates in grouped.items():
        selected_candidates = candidates
        if source_timestamp_field is not None and len(candidates) > 1:
            timestamped = [
                (_required_timestamp(candidate, source_timestamp_field, "selected"), candidate)
                for candidate in candidates
            ]
            latest_timestamp = max(timestamp for timestamp, _candidate in timestamped)
            selected_candidates = [
                candidate for timestamp, candidate in timestamped if timestamp == latest_timestamp
            ]
            if len(selected_candidates) == 1:
                same_day_resolved_counts[available] += len(candidates) - 1
        for candidate in selected_candidates:
            events_by_available[available].append((key, candidate))

    return dict(events_by_available), dict(availability_counts), dict(same_day_resolved_counts)


def _select_latest_active_row(
    key: tuple[str, ...],
    rows: list[dict[str, object]],
    *,
    availability_field: str,
    revision_field: str | None,
) -> dict[str, object]:
    latest_available = max(str(row.get(availability_field) or "") for row in rows)
    available_rows = [row for row in rows if str(row.get(availability_field) or "") == latest_available]
    if revision_field:
        latest_revision = max(str(row.get(revision_field) or "") for row in available_rows)
        revision_rows = [row for row in available_rows if str(row.get(revision_field) or "") == latest_revision]
    else:
        revision_rows = available_rows
    if len(revision_rows) > 1:
        raise PitError(f"unresolved duplicate PIT rows for key={key}")
    return revision_rows[0]


def _resolve_same_day_source_timestamp_ties(
    rows: list[dict[str, object]],
    *,
    logical_key: list[str],
    availability_field: str,
    revision_field: str | None,
    source_timestamp_field: str | None,
    decision_date: str,
) -> tuple[list[dict[str, object]], int]:
    if source_timestamp_field is None:
        return rows, 0

    passthrough: list[dict[str, object]] = []
    grouped: dict[tuple[str, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        available = _required_date(row, availability_field, "selected")
        if available > decision_date:
            passthrough.append(row)
            continue
        revision = _required_date(row, revision_field, "selected") if revision_field else ""
        grouped[_logical_key(row, logical_key) + (available, revision)].append(row)

    output = list(passthrough)
    resolved_count = 0
    for candidates in grouped.values():
        if len(candidates) == 1:
            output.append(candidates[0])
            continue

        timestamped: list[tuple[datetime, dict[str, object]]] = []
        for candidate in candidates:
            timestamped.append((_required_timestamp(candidate, source_timestamp_field, "selected"), candidate))
        latest_timestamp = max(timestamp for timestamp, _candidate in timestamped)
        latest_rows = [candidate for timestamp, candidate in timestamped if timestamp == latest_timestamp]
        output.extend(latest_rows)
        if len(latest_rows) == 1:
            resolved_count += len(candidates) - 1

    return output, resolved_count


def _collapse_raw_same_day_timestamp_duplicates(
    rows: list[dict[str, object]],
    *,
    logical_key: list[str],
    source_timestamp_field: str,
) -> tuple[list[dict[str, object]], int]:
    """Resolve one normalized raw key to its latest distinct source timestamp.

    The raw contract uses date-only availability, while AINVFINB may contain
    multiple intraday ``key3`` timestamps for the same canonical raw key.
    Persisting both would violate the raw artifact key.  A unique latest source
    timestamp is the PIT-safe daily representation; a tie at that timestamp is
    deliberately left unresolved for the artifact contract to reject.
    """
    grouped: dict[tuple[str, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[_logical_key(row, logical_key)].append(row)

    output: list[dict[str, object]] = []
    resolved_count = 0
    for candidates in grouped.values():
        if len(candidates) == 1:
            output.append(candidates[0])
            continue
        timestamped = [
            (_required_timestamp(candidate, source_timestamp_field, "financial_statement_raw"), candidate)
            for candidate in candidates
        ]
        latest_timestamp = max(timestamp for timestamp, _candidate in timestamped)
        latest_rows = [
            candidate
            for timestamp, candidate in timestamped
            if timestamp == latest_timestamp
        ]
        output.extend(latest_rows)
        if len(latest_rows) == 1:
            resolved_count += len(candidates) - 1
    return output, resolved_count


def _source_timestamp_field_for(selected_family_id: str, rule: dict[str, object]) -> str | None:
    tie_breaker = rule.get("source_timestamp_tie_breaker")
    if isinstance(tie_breaker, dict):
        field = _optional_rule_text(tie_breaker.get("field"))
        if field:
            return field
    if selected_family_id == "financial_statement_pit_selected":
        return "key3"
    if selected_family_id == "self_reported_numbers_pit_selected":
        return "annd"
    return None


def _rule_for(family_id: str, pit_registry: dict[str, object]) -> dict[str, object]:
    families = pit_registry.get("families")
    if not isinstance(families, dict):
        raise RawFamilyError("pit_registry must contain families")
    rule = families.get(family_id)
    if not isinstance(rule, dict):
        raise RawFamilyError(f"missing PIT registry rule for {family_id}")
    if "availability_field" not in rule:
        raise RawFamilyError(f"missing PIT registry availability_field for {family_id}")
    if not isinstance(rule.get("logical_key"), list) or not rule["logical_key"]:
        raise RawFamilyError(f"missing PIT registry logical_key for {family_id}")
    return rule


def _selected_rule_for(selected_family_id: str, pit_registry: dict[str, object]) -> dict[str, object]:
    try:
        return _rule_for(selected_family_id, pit_registry)
    except RawFamilyError:
        if selected_family_id == "self_reported_numbers_pit_selected":
            return {
                "availability_field": "source_available_date",
                "logical_key": ["ticker", "key3", "sem", "curr", "merg", "period_end_date"],
                "revision_field": "revision_date",
            }
        if selected_family_id == "financial_statement_pit_selected":
            return {
                "availability_field": "source_available_date",
                "logical_key": ["ticker", "no", "sem", "curr", "merg", "period_end_date"],
                "revision_field": "revision_date",
                "source_timestamp_tie_breaker": {
                    "field": "key3",
                    "scope": "same_normalized_source_available_date",
                    "order": "max_timestamp",
                },
            }
        raise


def _normalize_decision_date(decision_date: str) -> str:
    try:
        value = normalize_date(decision_date)
    except PitError as exc:
        raise RawFamilyError(f"invalid decision_date: {decision_date!r}") from exc
    if value is None:
        raise RawFamilyError("blank decision_date")
    return value


def _optional_rule_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_timestamp(row: dict[str, object], field: str, family_id: str) -> datetime:
    if field not in row:
        raise RawFamilyError(f"missing required PIT timestamp for {family_id}: {field}")
    value = row[field]
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise RawFamilyError(f"blank required PIT timestamp for {family_id}: {field}")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RawFamilyError(f"invalid PIT timestamp for {family_id}.{field}: {value!r}") from exc
    else:
        raise RawFamilyError(f"invalid PIT timestamp for {family_id}.{field}: {value!r}")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _duplicate_count(rows: list[dict[str, object]], logical_key: list[str]) -> int:
    counts = Counter(_logical_key(row, logical_key) for row in rows)
    return sum(count - 1 for count in counts.values() if count > 1)


def _logical_key(row: dict[str, object], logical_key: list[str]) -> tuple[str, ...]:
    values: list[str] = []
    for field in logical_key:
        if field not in row:
            raise RawFamilyError(f"missing logical key field after normalization: {field}")
        value = str(row.get(field) or "").strip()
        if not value:
            raise RawFamilyError(f"blank logical key field after normalization: {field}")
        values.append(value)
    return tuple(values)


def _add_date_range(diagnostics: dict[str, Any], rows: list[dict[str, object]], field: str) -> None:
    dates = [str(row[field]) for row in rows if row.get(field)]
    if not dates:
        diagnostics.setdefault("date_min", None)
        diagnostics.setdefault("date_max", None)
        return
    diagnostics.setdefault("date_min", min(dates))
    diagnostics.setdefault("date_max", max(dates))


_NORMALIZERS = {
    "trading_calendar": _normalize_trading_calendar,
    "daily_tradability": _normalize_daily_panel,
    "daily_chip": _normalize_daily_panel,
    "monthly_sales": _normalize_monthly_sales,
    "financial_statement_raw": _normalize_financial_statement,
    "self_reported_numbers_raw": _normalize_self_reported_numbers,
    "taiwan_index_futures_near_month": _normalize_futures_near_month,
}


_GENERIC_MDATE_FAMILY_IDS = {
    "director_supervisor_holdings",
    "board_reelection_statistics",
    "executive_change_events",
    "merger_acquisition_events",
    "private_placement_relation_events",
    "insider_transfer_completed",
    "insider_transfer_declared_not_completed",
    "treasury_stock_events",
}
