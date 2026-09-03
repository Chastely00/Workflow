from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime


class PitError(ValueError):
    pass


def normalize_date(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError as exc:
            raise PitError(f"unsupported date value: {value!r}") from exc
    if hasattr(value, "date"):
        parsed = value.date()
        if isinstance(parsed, date):
            return parsed.isoformat()
    raise PitError(f"unsupported date value: {value!r}")


def select_latest_pit_rows(
    rows: list[dict[str, object]],
    *,
    logical_key: list[str],
    availability_field: str,
    revision_field: str | None,
    decision_date: str,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    normalized_decision_date = normalize_date(decision_date)
    if normalized_decision_date is None:
        raise PitError("decision_date is required")

    diagnostics = {
        "input_row_count": len(rows),
        "eligible_row_count": 0,
        "future_row_count": 0,
        "selected_row_count": 0,
        "resolved_duplicate_count": 0,
        "unresolved_duplicate_count": 0,
    }
    grouped: dict[tuple[str, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        available = _required_date(row, availability_field, "availability_field")
        normalized = dict(row)
        normalized[availability_field] = available
        if revision_field:
            revision = _required_date(row, revision_field, "revision_field")
            normalized[revision_field] = revision
        if available > normalized_decision_date:
            diagnostics["future_row_count"] += 1
            continue
        diagnostics["eligible_row_count"] += 1
        grouped[_logical_key(normalized, logical_key)].append(normalized)

    selected: list[dict[str, object]] = []
    for key, candidates in grouped.items():
        latest_available = max(str(row.get(availability_field) or "") for row in candidates)
        available_rows = [
            row for row in candidates if str(row.get(availability_field) or "") == latest_available
        ]
        if revision_field:
            latest_revision = max(str(row.get(revision_field) or "") for row in available_rows)
            revision_rows = [
                row for row in available_rows if str(row.get(revision_field) or "") == latest_revision
            ]
        else:
            revision_rows = available_rows
        if len(revision_rows) > 1:
            diagnostics["unresolved_duplicate_count"] += len(revision_rows)
            raise PitError(f"unresolved duplicate PIT rows for key={key}")
        if len(candidates) > 1:
            diagnostics["resolved_duplicate_count"] += len(candidates) - 1
        selected.append(revision_rows[0])
    diagnostics["selected_row_count"] = len(selected)
    return (
        sorted(selected, key=lambda row: _logical_key(row, logical_key)),
        diagnostics,
    )


def _required_date(row: dict[str, object], field: str, role: str) -> str:
    if field not in row:
        raise PitError(f"missing {role}: {field}")
    normalized = normalize_date(row[field])
    if normalized is None:
        raise PitError(f"blank {role}: {field}")
    return normalized


def _logical_key(row: dict[str, object], columns: list[str]) -> tuple[str, ...]:
    values: list[str] = []
    for column in columns:
        if column not in row:
            raise PitError(f"missing logical_key: {column}")
        value = str(row[column]).strip() if row[column] is not None else ""
        if not value:
            raise PitError(f"blank logical_key: {column}")
        values.append(value)
    return tuple(values)
